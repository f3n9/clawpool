#!/usr/bin/env python3
import base64
import hashlib
import json
import http.client
import ipaddress
import os
from pathlib import Path
import re
import secrets
import select
import shlex
import socket
import struct
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DEFAULT_OPERATOR_SCOPES = [
    "operator.admin",
    "operator.read",
    "operator.write",
    "operator.approvals",
    "operator.pairing",
]

DEFAULT_CHANNEL_PLUGIN_DIRS = []

PLUGIN_ID_RE = re.compile(r"^[a-z0-9._-]+$")

DEFAULT_OPENAI_MODEL_IDS = [
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.3-chat",
]

DEFAULT_DASHSCOPE_MODEL_IDS = [
    "MiniMax-M2.5",
    "kimi-k2.5",
    "deepseek-v3.2",
    "qwen3.5-flash",
]

DEFAULT_DASHSCOPE_COMPAT_ENDPOINT = (
    "https://dashscope-yxai.hatch.yinxiang.com/compatible-mode/v1/chat/completions"
)

class StartupThrottle:
    def __init__(self, max_concurrent):
        self.max_concurrent = max(1, int(max_concurrent))
        self._active = 0
        self._lock = threading.Lock()

    def try_acquire(self):
        with self._lock:
            if self._active >= self.max_concurrent:
                return False
            self._active += 1
            return True

    def release(self):
        with self._lock:
            self._active = max(0, self._active - 1)


class DockerAPIError(RuntimeError):
    def __init__(self, status, reason, path):
        super().__init__(f"docker api error: {status} {reason} path={path}")
        self.status = status
        self.reason = reason
        self.path = path


class UnixSocketHTTPConnection(http.client.HTTPConnection):
    def __init__(self, unix_socket_path):
        super().__init__("localhost")
        self.unix_socket_path = unix_socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.unix_socket_path)


class UnixSocketTransport:
    def __init__(self, socket_path="/var/run/docker.sock"):
        self.socket_path = socket_path

    def request(self, method, path, body=None):
        conn = UnixSocketHTTPConnection(self.socket_path)
        headers = {}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        if resp.status >= 400:
            raise DockerAPIError(resp.status, resp.reason, path)
        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def stream(self, method, path, body=None):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.socket_path)
        headers = {"Host": "localhost", "Connection": "keep-alive"}
        payload = b""
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(payload))
        else:
            headers["Content-Length"] = "0"
        lines = [f"{method} {path} HTTP/1.1"] + [f"{k}: {v}" for k, v in headers.items()]
        req = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + payload
        sock.sendall(req)

        response = b""
        while b"\r\n\r\n" not in response:
            chunk = sock.recv(4096)
            if not chunk:
                sock.close()
                raise RuntimeError("docker stream response closed before headers")
            response += chunk
            if len(response) > 65536:
                sock.close()
                raise RuntimeError("docker stream response headers too large")

        head, tail = response.split(b"\r\n\r\n", 1)
        lines = head.decode("iso-8859-1", errors="replace").split("\r\n")
        status_line = lines[0] if lines else ""
        parts = status_line.split(" ", 2)
        status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        reason = parts[2] if len(parts) > 2 else ""
        if status >= 400:
            sock.close()
            raise DockerAPIError(status, reason or "docker stream error", path)
        return sock, tail


class DockerAPIClient:
    def __init__(self, transport=None):
        self.transport = transport or UnixSocketTransport()

    def inspect(self, name, wait=False):
        suffix = "?wait=1" if wait else ""
        return self.transport.request("GET", f"/containers/{name}/json{suffix}")

    def start(self, name):
        return self.transport.request("POST", f"/containers/{name}/start")

    def create(self, name, body):
        return self.transport.request("POST", f"/containers/create?name={name}", body=body)

    def create_exec(self, name, cmd, user="node", tty=True):
        body = {
            "AttachStdin": True,
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": tty,
            "Cmd": cmd,
            "User": user,
        }
        return self.transport.request("POST", f"/containers/{name}/exec", body=body)

    def start_exec_stream(self, exec_id, tty=True):
        body = {"Detach": False, "Tty": tty}
        return self.transport.stream("POST", f"/exec/{exec_id}/start", body=body)

    def resize_exec(self, exec_id, cols, rows):
        width = max(1, int(cols))
        height = max(1, int(rows))
        return self.transport.request("POST", f"/exec/{exec_id}/resize?h={height}&w={width}")

    def exec_run(self, name, cmd, user="node", timeout_seconds=20):
        payload = {
            "AttachStdout": False,
            "AttachStderr": False,
            "Tty": False,
            "Cmd": cmd,
            "User": user,
        }
        created = self.transport.request("POST", f"/containers/{name}/exec", body=payload)
        exec_id = created.get("Id") if isinstance(created, dict) else None
        if not exec_id:
            return None
        self.transport.request("POST", f"/exec/{exec_id}/start", body={"Detach": True, "Tty": False})
        deadline = time.time() + max(1, int(timeout_seconds))
        while time.time() < deadline:
            info = self.transport.request("GET", f"/exec/{exec_id}/json")
            if not isinstance(info, dict):
                return None
            if not info.get("Running", False):
                return info.get("ExitCode")
            time.sleep(0.2)
        return None


class DockerClient:
    """In-memory fallback for tests and environments without Docker socket."""

    def __init__(self):
        self._state = {}

    def start(self, name):
        self._state[name] = True

    def inspect(self, name, wait=False):
        if name not in self._state:
            raise DockerAPIError(404, "Not Found", f"/containers/{name}/json")
        running = self._state.get(name, False)
        return {"State": {"Running": running, "Health": {"Status": "healthy" if running else "starting"}}}

    def create(self, name, body):
        self._state[name] = False
        return {"Id": name}

    def exec_run(self, name, cmd, user="node", timeout_seconds=20):
        if name not in self._state:
            raise DockerAPIError(404, "Not Found", f"/containers/{name}/exec")
        return 0

    def create_exec(self, name, cmd, user="node", tty=True):
        if name not in self._state:
            raise DockerAPIError(404, "Not Found", f"/containers/{name}/exec")
        return {"Id": f"fake-{name}-exec"}

    def start_exec_stream(self, exec_id, tty=True):
        raise RuntimeError("docker stream not available in in-memory docker client")

    def resize_exec(self, exec_id, cols, rows):
        return None


def resolve_container_name(employee_id, user_sub, mapping):
    raw_identity = employee_id or user_sub
    if not raw_identity:
        raise ValueError("missing identity")
    identity = normalize_identity(raw_identity)
    if raw_identity in mapping:
        return mapping[raw_identity]
    if identity in mapping:
        return mapping[identity]
    return f"openclaw-{identity}"


def extract_identity(headers):
    employee_id = (
        headers.get("X-Employee-Id")
        or headers.get("X-Auth-Request-Email")
        or headers.get("X-Forwarded-Email")
        or headers.get("X-Auth-Request-User")
        or headers.get("X-Forwarded-User")
    )
    user_sub = headers.get("X-User-Sub")
    return employee_id, user_sub


def should_allow_loopback_query_identity(client_address, header_identity, header_sub):
    if header_identity or header_sub:
        return False
    if not client_address:
        return False
    host = client_address[0] if isinstance(client_address, (tuple, list)) else str(client_address)
    if not isinstance(host, str) or not host.strip():
        return False
    try:
        return ipaddress.ip_address(host.strip()).is_loopback
    except ValueError:
        return False


def normalize_identity(identity):
    raw = (identity or "").strip().lower()
    if not raw:
        raise ValueError("missing identity")
    safe = re.sub(r"[^a-z0-9._-]+", "-", raw)
    safe = re.sub(r"-{2,}", "-", safe).strip("-.")
    if not safe:
        raise ValueError("invalid identity")
    # Keep deterministic names while avoiding extreme-length container names.
    return safe[:96]


def classify_instance_lifecycle(provision_state, startup_state):
    if provision_state == "created":
        return "new"
    if startup_state == "running":
        return "running"
    if startup_state == "started":
        return "restart"
    return "unknown"


def emit_identity_audit(event, **fields):
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    print(json.dumps(payload, ensure_ascii=True), flush=True)


def split_csv_values(text):
    if not text:
        return []
    return [v.strip() for v in text.split(",") if v.strip()]


def _is_valid_plugin_id(plugin_id):
    return bool(isinstance(plugin_id, str) and PLUGIN_ID_RE.match(plugin_id.strip()))


def _resolve_channel_plugin_id(plugin_dir):
    plugin_name = Path(plugin_dir).name
    package_json_path = Path(plugin_dir) / "package.json"
    try:
        package_json = json.loads(package_json_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, TypeError):
        return plugin_name
    openclaw = package_json.get("openclaw")
    if not isinstance(openclaw, dict):
        return plugin_name
    channel = openclaw.get("channel")
    if not isinstance(channel, dict):
        return plugin_name
    channel_id = channel.get("id")
    if _is_valid_plugin_id(channel_id):
        return channel_id.strip()
    return plugin_name


def _resolve_default_channel_plugin_dirs():
    configured = split_csv_values(os.getenv("OPENCLAW_DEFAULT_CHANNEL_PLUGIN_DIRS", ""))
    return configured or list(DEFAULT_CHANNEL_PLUGIN_DIRS)


def _discover_channel_plugin_ids(plugin_dirs=None):
    discovered = []
    for plugin_dir in plugin_dirs or _resolve_default_channel_plugin_dirs():
        try:
            root = Path(plugin_dir)
        except TypeError:
            continue
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir(), key=lambda item: item.name):
            if not entry.is_dir() or not _is_valid_plugin_id(entry.name):
                continue
            discovered.append(_resolve_channel_plugin_id(entry))
    return _merge_unique_str_values(discovered)


def _default_channel_plugin_ids():
    configured = split_csv_values(os.getenv("OPENCLAW_DEFAULT_CHANNEL_PLUGINS", ""))
    discovered = _discover_channel_plugin_ids()
    return _merge_unique_str_values([*configured, *discovered])


def _ensure_default_channel_plugins(cfg, plugin_ids):
    plugins = cfg.get("plugins")
    if not isinstance(plugins, dict):
        plugins = {}
        cfg["plugins"] = plugins
    entries = plugins.get("entries")
    if not isinstance(entries, dict):
        entries = {}
        plugins["entries"] = entries
    channels = cfg.get("channels")
    if not isinstance(channels, dict):
        channels = {}
        cfg["channels"] = channels
    for plugin_id in _merge_unique_str_values(plugin_ids):
        if not _is_valid_plugin_id(plugin_id):
            continue
        entry = entries.get(plugin_id)
        if not isinstance(entry, dict):
            entry = {}
            entries[plugin_id] = entry
        if not isinstance(entry.get("enabled"), bool):
            entry["enabled"] = True
        channel_cfg = channels.get(plugin_id)
        if not isinstance(channel_cfg, dict):
            channel_cfg = {}
            channels[plugin_id] = channel_cfg
        if not isinstance(channel_cfg.get("enabled"), bool):
            channel_cfg["enabled"] = True


def is_websocket_upgrade(headers):
    # Some reverse-proxy chains may drop/reshape Connection while still forwarding
    # websocket handshake keys. Treat the request as websocket if either pattern matches.
    upgrade = (headers.get("Upgrade") or "").strip().lower()
    connection = (headers.get("Connection") or "").strip().lower()
    ws_key = (headers.get("Sec-WebSocket-Key") or "").strip()
    if upgrade == "websocket" and "upgrade" in connection:
        return True
    return bool(ws_key and upgrade == "websocket")


def is_browser_navigation_request(method, headers):
    if method != "GET":
        return False
    accept = (headers.get("Accept") or "").strip().lower()
    return "text/html" in accept


def is_retryable_upstream_error(exc):
    if isinstance(exc, ConnectionRefusedError):
        return True
    text = str(exc).strip().lower()
    if not text:
        return False
    markers = (
        "connection refused",
        "[errno 111]",
        "failed to establish a new connection",
        "upstream closed before websocket headers",
    )
    return any(marker in text for marker in markers)


def normalize_next_path(raw):
    value = (raw or "").strip()
    if not value:
        return "/"
    parsed = urlparse(value)
    path = parsed.path or "/"
    query = parsed.query
    if not path.startswith("/"):
        path = "/"
        query = ""
    if path == "/__openclaw__/bootstrap-status":
        path = "/"
        query = ""
    if query:
        return f"{path}?{query}"
    return path


def is_upstream_ready(container):
    upstream_port = int(os.getenv("OPENCLAW_INSTANCE_PORT", "18789"))
    conn = None
    try:
        conn = http.client.HTTPConnection(container, upstream_port, timeout=3)
        conn.request("GET", "/__openclaw__/control-ui-config.json")
        resp = conn.getresponse()
        resp.read()
        return 200 <= resp.status < 500
    except Exception:
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _websocket_accept_key(sec_key):
    seed = f"{sec_key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11".encode("utf-8")
    return base64.b64encode(hashlib.sha1(seed).digest()).decode("ascii")


def _ws_read_exact(sock, size):
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("websocket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _ws_read_frame(sock):
    head = _ws_read_exact(sock, 2)
    b1, b2 = head[0], head[1]
    opcode = b1 & 0x0F
    masked = bool(b2 & 0x80)
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", _ws_read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _ws_read_exact(sock, 8))[0]
    mask_key = _ws_read_exact(sock, 4) if masked else b""
    payload = _ws_read_exact(sock, length) if length else b""
    if masked and payload:
        payload = bytes(payload[i] ^ mask_key[i % 4] for i in range(len(payload)))
    return opcode, payload


def _ws_send_frame(sock, payload, opcode=2):
    if payload is None:
        payload = b""
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
        opcode = 1
    length = len(payload)
    head = bytearray()
    head.append(0x80 | (opcode & 0x0F))
    if length < 126:
        head.append(length)
    elif length <= 0xFFFF:
        head.append(126)
        head.extend(struct.pack("!H", length))
    else:
        head.append(127)
        head.extend(struct.pack("!Q", length))
    sock.sendall(bytes(head) + payload)


def _parse_console_control(payload):
    if not payload or len(payload) > 1024:
        return None
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("type") != "resize":
        return None
    try:
        cols = int(data.get("cols"))
        rows = int(data.get("rows"))
    except (TypeError, ValueError):
        return None
    if cols < 1 or rows < 1 or cols > 800 or rows > 1000:
        return None
    return {"type": "resize", "cols": cols, "rows": rows}


def extract_groups(headers):
    raw = headers.get("X-Forwarded-Groups") or headers.get("X-Auth-Request-Groups") or ""
    if not raw:
        return []
    # oauth2-proxy/IdP may separate groups by comma, semicolon, or whitespace.
    return [v for v in re.split(r"[,\s;]+", raw) if v]


def is_identity_allowed(headers, allowed_email_domains, allowed_groups):
    if not allowed_email_domains and not allowed_groups:
        return True

    email = (headers.get("X-Forwarded-Email") or headers.get("X-Auth-Request-Email") or "").strip()
    groups = set(extract_groups(headers))

    email_ok = True
    if allowed_email_domains:
        email_ok = False
        if email and "@" in email:
            domain = email.rsplit("@", 1)[1].lower()
            email_ok = domain in {d.lower() for d in allowed_email_domains}

    group_ok = True
    if allowed_groups:
        group_ok = any(g in groups for g in allowed_groups)

    return email_ok and group_ok


def _trusted_proxy_user_header_name():
    return (
        os.getenv("OPENCLAW_GATEWAY_TRUSTED_PROXY_USER_HEADER", "host").strip().lower() or "host"
    )


def _pick_identity_header_value(headers):
    for name in (
        "X-Employee-Id",
        "X-Forwarded-User",
        "X-Auth-Request-User",
        "X-Forwarded-Email",
        "X-Auth-Request-Email",
    ):
        value = headers.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _inject_trusted_proxy_user_header_if_needed(headers):
    target = _trusted_proxy_user_header_name()
    if target == "host":
        return

    exists = False
    for key in headers.keys():
        if key.lower() == target:
            value = headers.get(key)
            if isinstance(value, str) and value.strip():
                exists = True
                break
    if exists:
        return

    value = _pick_identity_header_value(headers)
    if value:
        headers[target] = value


def _safe_mkdir(path, mode):
    os.makedirs(path, exist_ok=True)
    os.chmod(path, mode)


def _safe_chown(path, uid, gid):
    os.chown(path, int(uid), int(gid))


def _write_if_missing(path, value, mode, uid, gid):
    if os.path.exists(path):
        os.chmod(path, mode)
        _safe_chown(path, uid, gid)
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{value}\n")
    os.chmod(path, mode)
    _safe_chown(path, uid, gid)


def _read_secret_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def _write_last_active_marker(identity, users_root):
    marker_dir = os.path.join(users_root, normalize_identity(identity), "runtime")
    os.makedirs(marker_dir, exist_ok=True)
    marker_path = os.path.join(marker_dir, "last_active_ts")
    with open(marker_path, "w", encoding="utf-8") as f:
        f.write(f"{int(time.time())}\n")
    os.chmod(marker_path, 0o600)
    container_uid = int(os.getenv("OPENCLAW_CONTAINER_UID", "1000"))
    container_gid = int(os.getenv("OPENCLAW_CONTAINER_GID", "1000"))
    _safe_chown(marker_path, container_uid, container_gid)


def _warm_local_pairing(docker, container):
    # Prewarm local device pairing so container-local CLI probes (openclaw status)
    # don't get stuck on "pairing required" for newly created/restarted instances.
    exec_run = getattr(docker, "exec_run", None)
    if not callable(exec_run):
        return
    cmd = [
        "sh",
        "-lc",
        "openclaw devices approve --latest >/dev/null 2>&1 || true",
    ]
    timeout_seconds = int(os.getenv("OPENCLAW_LOCAL_PAIRING_WARMUP_TIMEOUT_SECONDS", "10"))
    try:
        exec_run(container, cmd, user="node", timeout_seconds=timeout_seconds)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": "local_pairing_warmup_error",
                    "container": container,
                    "error": str(exc),
                },
                ensure_ascii=True,
            ),
            flush=True,
        )


def _warm_local_pairing_async(docker, container):
    thread = threading.Thread(
        target=_warm_local_pairing,
        args=(docker, container),
        daemon=True,
        name=f"pairing-warmup-{container}",
    )
    thread.start()
    return thread


def _wait_for_local_pairing_identity(runtime_dir, uid, gid, timeout_seconds=None):
    identity_path = os.path.join(runtime_dir, "identity", "device.json")
    paired_path = os.path.join(runtime_dir, "devices", "paired.json")
    if timeout_seconds is None:
        timeout_seconds = int(
            os.getenv(
                "OPENCLAW_LOCAL_PAIRING_REPAIR_TIMEOUT_SECONDS",
                os.getenv("OPENCLAW_HEALTH_TIMEOUT_SECONDS", "120"),
            )
        )
    poll_seconds = float(os.getenv("OPENCLAW_LOCAL_PAIRING_REPAIR_POLL_SECONDS", "0.5"))
    sleep_seconds = max(0.1, poll_seconds)
    deadline = time.time() + max(1, timeout_seconds)
    while time.time() < deadline:
        if not os.path.exists(identity_path):
            time.sleep(sleep_seconds)
            continue

        identity = _read_json_object(identity_path)
        device_id_raw = identity.get("deviceId")
        device_id = device_id_raw.strip() if isinstance(device_id_raw, str) else ""
        if device_id:
            paired = _read_json_object(paired_path)
            if isinstance(paired.get(device_id), dict):
                return

        try:
            _repair_local_device_pairing(runtime_dir, uid, gid)
        except OSError:
            pass

        if device_id:
            paired = _read_json_object(paired_path)
            if isinstance(paired.get(device_id), dict):
                return

        time.sleep(sleep_seconds)



def _schedule_local_pairing_repair(runtime_dir, uid, gid, timeout_seconds=None):
    thread = threading.Thread(
        target=_wait_for_local_pairing_identity,
        args=(runtime_dir, uid, gid, timeout_seconds),
        daemon=True,
        name=f"pairing-repair-{os.path.basename(runtime_dir)}",
    )
    thread.start()
    return thread


def _read_json_object(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        return payload if isinstance(payload, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json_object(path, payload, mode, uid, gid):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)
        f.write("\n")
    os.chmod(path, mode)
    _safe_chown(path, uid, gid)


def _merge_unique_str_values(values):
    out = []
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item or item in out:
            continue
        out.append(item)
    return out


DEFAULT_RUNTIME_WORKSPACE_ROOT = "~/.openclaw/workspace"
DEFAULT_SYSTEM_SKILLS_DIR = "/app/skills"
DEFAULT_RUNTIME_SKILLS_DIR = f"{DEFAULT_RUNTIME_WORKSPACE_ROOT}/skills"
DEFAULT_RUNTIME_PLUGINS_DIR = f"{DEFAULT_RUNTIME_WORKSPACE_ROOT}/plugins"
DEFAULT_RUNTIME_HOOKS_DIR = f"{DEFAULT_RUNTIME_WORKSPACE_ROOT}/hooks"
DEFAULT_RUNTIME_HOOKS_TRANSFORMS_DIR = f"{DEFAULT_RUNTIME_HOOKS_DIR}/transforms"
DEFAULT_RUNTIME_CRON_STORE = f"{DEFAULT_RUNTIME_WORKSPACE_ROOT}/data/cron/jobs.jsonl"


def _ensure_runtime_workspace_dirs(runtime_dir, uid, gid):
    for rel_path in (
        "workspace",
        os.path.join("workspace", "skills"),
        os.path.join("workspace", "plugins"),
        os.path.join("workspace", "hooks"),
        os.path.join("workspace", "hooks", "transforms"),
        os.path.join("workspace", "data"),
        os.path.join("workspace", "data", "cron"),
    ):
        target = os.path.join(runtime_dir, rel_path)
        _safe_mkdir(target, 0o700)
        _safe_chown(target, uid, gid)



def _public_key_raw_base64url_from_pem(public_key_pem):
    if not isinstance(public_key_pem, str):
        return ""
    body = "".join(
        line.strip()
        for line in public_key_pem.splitlines()
        if line and not line.startswith("-----")
    )
    if not body:
        return ""
    try:
        der = base64.b64decode(body)
    except Exception:
        return ""
    if len(der) < 32:
        return ""
    raw = der[-32:]
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _normalize_openai_compatible_base_url(url, default_url):
    value = (url or "").strip() or default_url
    parsed = urlparse(value)
    suffix = "/chat/completions"
    path = parsed.path or ""
    if path.endswith(suffix):
        trimmed_path = path[: -len(suffix)] or "/"
        parsed = parsed._replace(path=trimmed_path, params="", query="", fragment="")
        value = parsed.geturl()
    return value.rstrip("/")


def _normalize_model_ref(model_ref, dashscope_model_ids=None, default_provider="openai"):
    if not isinstance(model_ref, str):
        return ""
    ref = model_ref.strip()
    if not ref:
        return ""
    if "/" in ref:
        provider, model_id = ref.split("/", 1)
        provider = provider.strip().lower()
        model_id = model_id.strip()
        return f"{provider}/{model_id}" if provider and model_id else ""
    dashscope_lookup = {candidate: candidate for candidate in (dashscope_model_ids or DEFAULT_DASHSCOPE_MODEL_IDS)}
    canonical_dashscope = dashscope_lookup.get(ref)
    if canonical_dashscope:
        return f"dashscope/{canonical_dashscope}"
    return f"{default_provider}/{ref}"


def _resolve_dashscope_api_key():
    return os.getenv("OPENCLAW_DASHSCOPE_API_KEY", "").strip()


def _legacy_managed_primary_refs():
    return [
        "openai/gpt-5.3-chat",
        "openai/gpt-5.4",
        "dashscope/MiniMax-M2.5",
    ]


def _default_primary_model_ref():
    if _resolve_dashscope_api_key():
        return "dashscope/MiniMax-M2.5"
    return "openai/gpt-5.3-chat"


def _provider_model_ids(model_refs, provider, default_ids):
    out = []
    prefix = f"{provider}/"
    for model_ref in model_refs:
        if not isinstance(model_ref, str) or not model_ref.startswith(prefix):
            continue
        model_id = model_ref[len(prefix):].strip()
        if model_id and model_id not in out:
            out.append(model_id)
    return out or list(default_ids)


def _resolve_operator_scopes():
    configured = split_csv_values(
        os.getenv("OPENCLAW_OPERATOR_SCOPES", ",".join(DEFAULT_OPERATOR_SCOPES))
    )
    only_operator_scopes = [scope for scope in configured if scope.startswith("operator.")]
    merged = _merge_unique_str_values(only_operator_scopes)
    return merged or list(DEFAULT_OPERATOR_SCOPES)


def _repair_local_device_pairing(runtime_dir, uid, gid):
    identity_path = os.path.join(runtime_dir, "identity", "device.json")
    if not os.path.exists(identity_path):
        return

    identity = _read_json_object(identity_path)
    device_id_raw = identity.get("deviceId")
    device_id = device_id_raw.strip() if isinstance(device_id_raw, str) else ""
    if not device_id:
        return

    devices_dir = os.path.join(runtime_dir, "devices")
    _safe_mkdir(devices_dir, 0o700)
    _safe_chown(devices_dir, uid, gid)
    paired_path = os.path.join(devices_dir, "paired.json")
    pending_path = os.path.join(devices_dir, "pending.json")
    paired = _read_json_object(paired_path)
    pending = _read_json_object(pending_path)
    scopes = _resolve_operator_scopes()
    now_ms = int(time.time() * 1000)

    paired_changed = False
    paired_entry = paired.get(device_id)
    if not isinstance(paired_entry, dict):
        latest_pending = None
        latest_pending_ts = -1
        for request in pending.values():
            if not isinstance(request, dict):
                continue
            if request.get("deviceId") != device_id:
                continue
            ts = request.get("ts")
            ts_int = int(ts) if isinstance(ts, int) else -1
            if ts_int >= latest_pending_ts:
                latest_pending = request
                latest_pending_ts = ts_int
        if isinstance(latest_pending, dict):
            paired_entry = {
                "deviceId": device_id,
                "publicKey": latest_pending.get("publicKey"),
                "platform": latest_pending.get("platform"),
                "clientId": latest_pending.get("clientId") or "gateway-client",
                "clientMode": latest_pending.get("clientMode") or "backend",
                "role": "operator",
                "roles": ["operator"],
                "scopes": list(scopes),
                "approvedScopes": list(scopes),
                "createdAtMs": latest_pending.get("ts") or now_ms,
                "approvedAtMs": now_ms,
            }
            paired[device_id] = paired_entry
            paired_changed = True
        else:
            public_key = _public_key_raw_base64url_from_pem(identity.get("publicKeyPem"))
            if public_key:
                paired_entry = {
                    "deviceId": device_id,
                    "publicKey": public_key,
                    "platform": "linux",
                    "clientId": "gateway-client",
                    "clientMode": "backend",
                    "role": "operator",
                    "roles": ["operator"],
                    "scopes": list(scopes),
                    "approvedScopes": list(scopes),
                    "tokens": {
                        "operator": {
                            "token": secrets.token_urlsafe(32),
                            "role": "operator",
                            "scopes": list(scopes),
                            "createdAtMs": now_ms,
                        }
                    },
                    "createdAtMs": now_ms,
                    "approvedAtMs": now_ms,
                }
                paired[device_id] = paired_entry
                paired_changed = True

    if isinstance(paired_entry, dict):
        roles = _merge_unique_str_values([*(paired_entry.get("roles") or []), "operator"])
        if paired_entry.get("roles") != roles:
            paired_entry["roles"] = roles
            paired_changed = True
        if paired_entry.get("role") != "operator":
            paired_entry["role"] = "operator"
            paired_changed = True

        merged_scopes = _merge_unique_str_values([*(paired_entry.get("scopes") or []), *scopes])
        if paired_entry.get("scopes") != merged_scopes:
            paired_entry["scopes"] = merged_scopes
            paired_changed = True

        approved_scopes = _merge_unique_str_values(
            [*(paired_entry.get("approvedScopes") or []), *scopes]
        )
        if paired_entry.get("approvedScopes") != approved_scopes:
            paired_entry["approvedScopes"] = approved_scopes
            paired_changed = True

        tokens = paired_entry.get("tokens")
        if isinstance(tokens, dict):
            operator_token = tokens.get("operator")
            if isinstance(operator_token, dict):
                if operator_token.get("role") != "operator":
                    operator_token["role"] = "operator"
                    paired_changed = True
                token_scopes = _merge_unique_str_values([*(operator_token.get("scopes") or []), *scopes])
                if operator_token.get("scopes") != token_scopes:
                    operator_token["scopes"] = token_scopes
                    paired_changed = True

    pending_changed = False
    if pending:
        remove_ids = []
        for request_id, request in pending.items():
            if isinstance(request, dict) and request.get("deviceId") == device_id:
                remove_ids.append(request_id)
        if remove_ids:
            for request_id in remove_ids:
                pending.pop(request_id, None)
            pending_changed = True

    if paired_changed:
        _write_json_object(paired_path, paired, 0o600, uid, gid)
    elif os.path.exists(paired_path):
        os.chmod(paired_path, 0o600)
        _safe_chown(paired_path, uid, gid)

    if pending_changed:
        _write_json_object(pending_path, pending, 0o600, uid, gid)
    elif os.path.exists(pending_path):
        os.chmod(pending_path, 0o600)
        _safe_chown(pending_path, uid, gid)


def _ensure_runtime_config(runtime_dir, uid, gid, gateway_token=""):
    config_path = os.path.join(runtime_dir, "openclaw.json")
    cfg = {}
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except (json.JSONDecodeError, OSError):
            cfg = {}

    if not isinstance(cfg, dict):
        cfg = {}
    gateway = cfg.get("gateway")
    if not isinstance(gateway, dict):
        gateway = {}
        cfg["gateway"] = gateway

    bind_mode = os.getenv("OPENCLAW_GATEWAY_BIND", "lan").strip() or "lan"
    gateway.setdefault("bind", bind_mode)

    try:
        gateway.setdefault("port", int(os.getenv("OPENCLAW_INSTANCE_PORT", "18789")))
    except ValueError:
        gateway.setdefault("port", 3000)

    control_ui = gateway.get("controlUi")
    if not isinstance(control_ui, dict):
        control_ui = {}
        gateway["controlUi"] = control_ui
    allowed_origins = control_ui.get("allowedOrigins")
    if not isinstance(allowed_origins, list) or not allowed_origins:
        explicit_origin = os.getenv("OPENCLAW_CONTROL_UI_ORIGIN", "").strip()
        host = os.getenv("OPENCLAW_HOST", "").strip()
        origin = explicit_origin or (f"https://{host}" if host else "")
        if origin:
            control_ui["allowedOrigins"] = [origin]

    auth = gateway.get("auth")
    if not isinstance(auth, dict):
        auth = {}
        gateway["auth"] = auth
    auth_mode = os.getenv("OPENCLAW_GATEWAY_AUTH_MODE", "trusted-proxy").strip() or "trusted-proxy"
    if not isinstance(auth.get("mode"), str) or auth.get("mode") != auth_mode:
        auth["mode"] = auth_mode
    token = (gateway_token or os.getenv("OPENCLAW_GATEWAY_AUTH_TOKEN", "").strip()).strip()
    if token and not auth.get("token"):
        auth["token"] = token

    if auth.get("mode") == "trusted-proxy":
        trusted_proxy = auth.get("trustedProxy")
        if not isinstance(trusted_proxy, dict):
            trusted_proxy = {}
        # Remove keys that were used in prior failed experiments and are rejected by OpenClaw schema.
        trusted_proxy.pop("emailHeader", None)
        trusted_proxy.pop("cidrs", None)
        configured_user_header = (
            os.getenv("OPENCLAW_GATEWAY_TRUSTED_PROXY_USER_HEADER", "host").strip() or "host"
        )
        user_header = trusted_proxy.get("userHeader")
        if not isinstance(user_header, str) or user_header.strip() != configured_user_header:
            trusted_proxy["userHeader"] = configured_user_header
        auth["trustedProxy"] = trusted_proxy

        trusted_proxies = gateway.get("trustedProxies")
        if not isinstance(trusted_proxies, list) or not trusted_proxies:
            gateway["trustedProxies"] = split_csv_values(
                os.getenv("OPENCLAW_GATEWAY_TRUSTED_PROXIES", "127.0.0.1/32,172.16.0.0/12")
            )

    # Default/bundled channel/plugin enablement is reconciled inside the user
    # container at startup, where the image-bundled extension manifest is
    # available. Avoid persisting guessed host-side defaults here.

    # Enable webchat image attachments by default for new/legacy users that do
    # not have an explicit preference yet.
    tools = cfg.get("tools")
    if not isinstance(tools, dict):
        tools = {}
        cfg["tools"] = tools
    # Force consistent tool capabilities/visibility for all users.
    tools["profile"] = "full"
    sessions = tools.get("sessions")
    if not isinstance(sessions, dict):
        sessions = {}
        tools["sessions"] = sessions
    sessions["visibility"] = "all"
    media = tools.get("media")
    if not isinstance(media, dict):
        media = {}
        tools["media"] = media
    image = media.get("image")
    if not isinstance(image, dict):
        image = {}
        media["image"] = image
    if not isinstance(image.get("enabled"), bool):
        image["enabled"] = True

    _ensure_runtime_workspace_dirs(runtime_dir, uid, gid)

    skills_cfg = cfg.get("skills")
    if not isinstance(skills_cfg, dict):
        skills_cfg = {}
        cfg["skills"] = skills_cfg
    skills_load = skills_cfg.get("load")
    if not isinstance(skills_load, dict):
        skills_load = {}
        skills_cfg["load"] = skills_load
    extra_skill_dirs = skills_load.get("extraDirs")
    merged_skill_dirs = _merge_unique_str_values(
        [DEFAULT_SYSTEM_SKILLS_DIR, DEFAULT_RUNTIME_SKILLS_DIR, *(extra_skill_dirs or [])]
    )
    if merged_skill_dirs:
        skills_load["extraDirs"] = merged_skill_dirs

    plugins_cfg = cfg.get("plugins")
    if not isinstance(plugins_cfg, dict):
        plugins_cfg = {}
        cfg["plugins"] = plugins_cfg
    plugins_load = plugins_cfg.get("load")
    if not isinstance(plugins_load, dict):
        plugins_load = {}
        plugins_cfg["load"] = plugins_load
    plugin_paths = plugins_load.get("paths")
    if not isinstance(plugin_paths, list) or not _merge_unique_str_values(plugin_paths):
        plugins_load["paths"] = [DEFAULT_RUNTIME_PLUGINS_DIR]

    hooks_cfg = cfg.get("hooks")
    if not isinstance(hooks_cfg, dict):
        hooks_cfg = {}
        cfg["hooks"] = hooks_cfg
    hooks_internal = hooks_cfg.get("internal")
    if not isinstance(hooks_internal, dict):
        hooks_internal = {}
        hooks_cfg["internal"] = hooks_internal
    hooks_internal_load = hooks_internal.get("load")
    if not isinstance(hooks_internal_load, dict):
        hooks_internal_load = {}
        hooks_internal["load"] = hooks_internal_load
    hook_extra_dirs = hooks_internal_load.get("extraDirs")
    if not isinstance(hook_extra_dirs, list) or not _merge_unique_str_values(hook_extra_dirs):
        hooks_internal_load["extraDirs"] = [DEFAULT_RUNTIME_HOOKS_DIR]
    transforms_dir = hooks_cfg.get("transformsDir")
    if not isinstance(transforms_dir, str) or not transforms_dir.strip():
        hooks_cfg["transformsDir"] = DEFAULT_RUNTIME_HOOKS_TRANSFORMS_DIR

    cron_cfg = cfg.get("cron")
    if not isinstance(cron_cfg, dict):
        cron_cfg = {}
        cfg["cron"] = cron_cfg
    cron_store = cron_cfg.get("store")
    if not isinstance(cron_store, str) or not cron_store.strip():
        cron_cfg["store"] = DEFAULT_RUNTIME_CRON_STORE

    # Ensure browser automation works out-of-the-box in headless containers so
    # agent/browser tasks (navigate/screenshot/pdf) are usable for new users.
    browser_cfg = cfg.get("browser")
    if not isinstance(browser_cfg, dict):
        browser_cfg = {}
        cfg["browser"] = browser_cfg
    default_browser_executable = (
        os.getenv("OPENCLAW_BROWSER_EXECUTABLE_PATH", "/usr/local/bin/openclaw-chromium").strip()
        or "/usr/local/bin/openclaw-chromium"
    )
    executable_path = browser_cfg.get("executablePath")
    if not isinstance(executable_path, str) or not executable_path.strip():
        browser_cfg["executablePath"] = default_browser_executable
    if not isinstance(browser_cfg.get("headless"), bool):
        browser_cfg["headless"] = True
    if not isinstance(browser_cfg.get("noSandbox"), bool):
        browser_cfg["noSandbox"] = True

    # Ensure users always get the intended provider set: the original OpenAI models
    # remain available, and the DashScope-compatible model set is added alongside them.
    desired_model = _normalize_model_ref(
        os.getenv("OPENCLAW_DEFAULT_OPENAI_MODEL", _default_primary_model_ref()),
        dashscope_model_ids=DEFAULT_DASHSCOPE_MODEL_IDS,
    ) or _default_primary_model_ref()

    allowed_models_raw = split_csv_values(os.getenv("OPENCLAW_ALLOWED_MODELS", ""))
    normalized_allowed_model_refs = [
        ref
        for ref in (
            _normalize_model_ref(model_ref, dashscope_model_ids=DEFAULT_DASHSCOPE_MODEL_IDS)
            for model_ref in allowed_models_raw
        )
        if ref
    ]

    dashscope_api_key = _resolve_dashscope_api_key()

    openai_model_ids = _provider_model_ids(
        normalized_allowed_model_refs,
        provider="openai",
        default_ids=DEFAULT_OPENAI_MODEL_IDS,
    )
    dashscope_model_ids = (
        _provider_model_ids(
            normalized_allowed_model_refs,
            provider="dashscope",
            default_ids=DEFAULT_DASHSCOPE_MODEL_IDS,
        )
        if dashscope_api_key
        else []
    )

    if desired_model.startswith("openai/"):
        desired_model_id = desired_model.split("/", 1)[1].strip()
        if desired_model_id and desired_model_id not in openai_model_ids:
            openai_model_ids.insert(0, desired_model_id)
    elif desired_model.startswith("dashscope/") and dashscope_api_key:
        desired_model_id = desired_model.split("/", 1)[1].strip()
        if desired_model_id and desired_model_id not in dashscope_model_ids:
            dashscope_model_ids.insert(0, desired_model_id)
    elif desired_model.startswith("dashscope/") and not dashscope_api_key:
        desired_model = "openai/gpt-5.3-chat"

    allowed_models = [
        *[f"openai/{model_id}" for model_id in openai_model_ids],
        *[f"dashscope/{model_id}" for model_id in dashscope_model_ids],
    ]

    agents = cfg.get("agents")
    if not isinstance(agents, dict):
        agents = {}
        cfg["agents"] = agents
    defaults = agents.get("defaults")
    if not isinstance(defaults, dict):
        defaults = {}
        agents["defaults"] = defaults
    model_cfg = defaults.get("model")
    if not isinstance(model_cfg, dict):
        model_cfg = {}
        defaults["model"] = model_cfg

    primary = model_cfg.get("primary")
    explicit_default_model = os.getenv("OPENCLAW_DEFAULT_OPENAI_MODEL", "").strip()
    should_set_primary = not isinstance(primary, str) or not primary.strip()
    if not should_set_primary and isinstance(primary, str):
        if primary.startswith("anthropic/"):
            should_set_primary = True
        elif allowed_models and primary not in allowed_models:
            should_set_primary = True
        elif explicit_default_model and primary != desired_model and primary in _legacy_managed_primary_refs():
            should_set_primary = True
    if should_set_primary:
        model_cfg["primary"] = desired_model
        primary = desired_model

    workspace = defaults.get("workspace")
    if not isinstance(workspace, str) or not workspace.strip():
        defaults["workspace"] = DEFAULT_RUNTIME_WORKSPACE_ROOT

    models_cfg = defaults.get("models")
    if not isinstance(models_cfg, dict):
        models_cfg = {}
        defaults["models"] = models_cfg
    stale_refs = [
        ref
        for ref in list(models_cfg.keys())
        if isinstance(ref, str)
        and (ref.startswith("openai/") or ref.startswith("dashscope/"))
        and ref not in allowed_models
    ]
    for ref in stale_refs:
        models_cfg.pop(ref, None)
    if isinstance(primary, str) and primary and primary not in models_cfg:
        models_cfg[primary] = {}

    models_root = cfg.get("models")
    if not isinstance(models_root, dict):
        models_root = {}
        cfg["models"] = models_root
    providers = models_root.get("providers")
    if not isinstance(providers, dict):
        providers = {}
        models_root["providers"] = providers

    openai_provider = providers.get("openai")
    if not isinstance(openai_provider, dict):
        openai_provider = {}
    openai_provider["baseUrl"] = (
        os.getenv("OPENCLAW_DEFAULT_OPENAI_ENDPOINT", "https://api.openai.com/v1").strip()
        or "https://api.openai.com/v1"
    )
    configured_openai_api = os.getenv("OPENCLAW_OPENAI_API", "").strip()
    openai_api = configured_openai_api or "openai-responses"
    openai_provider["api"] = openai_api
    openai_provider["models"] = [
        {
            "id": model_id,
            "name": model_id,
            "reasoning": True,
            "input": ["text", "image"],
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
            "contextWindow": 200000,
            "maxTokens": 32768,
        }
        for model_id in openai_model_ids
    ]
    providers["openai"] = openai_provider

    provider_models = {
        "openai": openai_model_ids,
    }
    if dashscope_api_key:
        dashscope_provider = providers.get("dashscope")
        if not isinstance(dashscope_provider, dict):
            dashscope_provider = {}
        dashscope_provider["baseUrl"] = _normalize_openai_compatible_base_url(
            os.getenv("OPENCLAW_DASHSCOPE_COMPAT_ENDPOINT", DEFAULT_DASHSCOPE_COMPAT_ENDPOINT),
            DEFAULT_DASHSCOPE_COMPAT_ENDPOINT,
        )
        dashscope_provider["api"] = "openai-completions"
        dashscope_provider["apiKey"] = dashscope_api_key
        dashscope_provider["models"] = [
            {
                "id": model_id,
                "name": model_id,
                "reasoning": True,
                "input": ["text", "image"],
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "contextWindow": 200000,
                "maxTokens": 32768,
            }
            for model_id in dashscope_model_ids
        ]
        providers["dashscope"] = dashscope_provider
        provider_models["dashscope"] = dashscope_model_ids
    else:
        providers.pop("dashscope", None)
    for provider_id, model_ids in provider_models.items():
        for model_id in model_ids:
            model_ref = f"{provider_id}/{model_id}"
            model_entry = models_cfg.get(model_ref)
            if not isinstance(model_entry, dict):
                model_entry = {}
                models_cfg[model_ref] = model_entry
            params = model_entry.get("params")
            if not isinstance(params, dict):
                params = {}
                model_entry["params"] = params
            params.setdefault("transport", "sse")
            params.setdefault("openaiWsWarmup", False)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=True, indent=2)
        f.write("\n")
    os.chmod(config_path, 0o600)
    _safe_chown(config_path, uid, gid)


def _ensure_user_gateway_token(identity, users_root):
    static_token = os.getenv("OPENCLAW_GATEWAY_AUTH_TOKEN", "").strip()
    if static_token:
        return static_token
    base = os.path.join(users_root, identity)
    secrets_dir = os.path.join(base, "secrets")
    container_uid = int(os.getenv("OPENCLAW_CONTAINER_UID", "1000"))
    container_gid = int(os.getenv("OPENCLAW_CONTAINER_GID", "1000"))
    _safe_mkdir(secrets_dir, 0o700)
    _safe_chown(secrets_dir, container_uid, container_gid)
    token_file = os.path.join(secrets_dir, "gateway_auth_token")
    token = ""
    if os.path.exists(token_file):
        token = _read_secret_file(token_file).strip()
    if not token:
        token = secrets.token_urlsafe(32)
        _write_if_missing(token_file, token, 0o600, container_uid, container_gid)
    else:
        os.chmod(token_file, 0o600)
        _safe_chown(token_file, container_uid, container_gid)
    return token


def _should_force_openai_responses_store():
    enabled = os.getenv("OPENCLAW_FORCE_RESPONSES_STORE", "true").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return False
    api = (os.getenv("OPENCLAW_OPENAI_API", "").strip() or "openai-responses").lower()
    return api == "openai-responses"


def _build_default_startup_cmd(start_cmd, force_responses_store):
    target = "/app/node_modules/@mariozechner/pi-ai/dist/providers/openai-responses.js"
    shared = "/app/node_modules/@mariozechner/pi-ai/dist/providers/openai-responses-shared.js"
    runtime_cfg = "/home/node/.openclaw/openclaw.json"
    install_compatibility_shims = """
const fs = require('fs');
const path = require('path');
const compatRoot = '/app/src/infra';
fs.mkdirSync(compatRoot, { recursive: true });
const writeCompatFile = (name, source) => {
  const target = path.join(compatRoot, name);
  if (!fs.existsSync(target)) {
    fs.writeFileSync(target, source);
  }
};
writeCompatFile('parse-finite-number.js', `export function parseFiniteNumber(value) {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number.parseFloat(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return undefined;
}

export function parseStrictPositiveInteger(value) {
  const parsed = parseFiniteNumber(value);
  return typeof parsed === "number" && Number.isInteger(parsed) && parsed > 0
    ? parsed
    : undefined;
}
`);
writeCompatFile('abort-signal.js', `export async function waitForAbortSignal(signal) {
  if (!signal || signal.aborted) {
    return;
  }
  await new Promise((resolve) => {
    const onAbort = () => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}
`);
"""
    reconcile_channel_plugins = """
const fs = require('fs');
const path = require('path');
const builtInManifestPath = '/app/extensions/.openclaw-builtins.json';
const configuredRoots = (process.env.OPENCLAW_DEFAULT_CHANNEL_PLUGIN_DIRS || '')
  .split(',')
  .map((value) => value.trim())
  .filter(Boolean);
const pluginRoots = configuredRoots;
const validPluginId = (value) => /^[a-z0-9._-]+$/.test(value);
const explicitPluginIds = (process.env.OPENCLAW_DEFAULT_CHANNEL_PLUGINS || '')
  .split(',')
  .map((value) => value.trim())
  .filter(validPluginId);
const discoveredPluginIds = [];
for (const root of pluginRoots) {
  try {
    if (!fs.existsSync(root) || !fs.statSync(root).isDirectory()) {
      continue;
    }
    for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
      if (entry.isDirectory() && validPluginId(entry.name)) {
        discoveredPluginIds.push(entry.name);
      }
    }
  } catch (error) {
  }
}
const manifestPayload = (() => {
  try {
    const manifest = JSON.parse(fs.readFileSync(builtInManifestPath, 'utf8'));
    return {
      builtInChannels: Array.isArray(manifest.builtInChannels)
        ? manifest.builtInChannels
        : (Array.isArray(manifest.channels) ? manifest.channels : []),
      bundledExtraPlugins: Array.isArray(manifest.bundledExtraPlugins) ? manifest.bundledExtraPlugins : [],
    };
  } catch (error) {
    return { builtInChannels: [], bundledExtraPlugins: [] };
  }
})();
const allBuiltInChannelIds = [];
const loadableBuiltInChannelIds = [];
for (const channelEntry of manifestPayload.builtInChannels) {
  if (!channelEntry || !validPluginId(channelEntry.channelId)) {
    continue;
  }
  if (!allBuiltInChannelIds.includes(channelEntry.channelId)) {
    allBuiltInChannelIds.push(channelEntry.channelId);
  }
  if (channelEntry.loadable && !loadableBuiltInChannelIds.includes(channelEntry.channelId)) {
    loadableBuiltInChannelIds.push(channelEntry.channelId);
  }
}
const extraPluginsById = new Map();
const legacyPluginAliases = new Map();
const rememberExtraPlugin = (pluginEntry) => {
  if (!pluginEntry || !validPluginId(pluginEntry.pluginId)) {
    return;
  }
  const pluginId = pluginEntry.pluginId;
  const existing = extraPluginsById.get(pluginId);
  const channelId = existing && validPluginId(existing.channelId)
    ? existing.channelId
    : (validPluginId(pluginEntry.channelId) ? pluginEntry.channelId : pluginId);
  if (channelId !== pluginId) {
    legacyPluginAliases.set(channelId, pluginId);
  }
  extraPluginsById.set(pluginId, {
    pluginId,
    channelId,
    loadable: pluginEntry.loadable !== false && (!existing || existing.loadable !== false),
  });
};
for (const pluginEntry of manifestPayload.bundledExtraPlugins) {
  rememberExtraPlugin(pluginEntry);
}
for (let pluginId of [...new Set([...explicitPluginIds, ...discoveredPluginIds])]) {
  pluginId = legacyPluginAliases.get(pluginId) || pluginId;
  if (allBuiltInChannelIds.includes(pluginId)) {
    continue;
  }
  rememberExtraPlugin({
    pluginId,
    channelId: pluginId,
    loadable: true,
  });
}
let cfg = {};
try {
  if (fs.existsSync('RUNTIME_CFG')) {
    cfg = JSON.parse(fs.readFileSync('RUNTIME_CFG', 'utf8'));
  }
} catch (error) {
  cfg = {};
}
if (!cfg || typeof cfg !== 'object' || Array.isArray(cfg)) {
  cfg = {};
}
if (!cfg.plugins || typeof cfg.plugins !== 'object' || Array.isArray(cfg.plugins)) {
  cfg.plugins = {};
}
if (!cfg.plugins.entries || typeof cfg.plugins.entries !== 'object' || Array.isArray(cfg.plugins.entries)) {
  cfg.plugins.entries = {};
}
if (!Array.isArray(cfg.plugins.allow)) {
  cfg.plugins.allow = [];
}
if (!cfg.channels || typeof cfg.channels !== 'object' || Array.isArray(cfg.channels)) {
  cfg.channels = {};
}
cfg.plugins.allow = cfg.plugins.allow.filter((pluginId) => !allBuiltInChannelIds.includes(pluginId));
for (const channelId of allBuiltInChannelIds) {
  if (loadableBuiltInChannelIds.includes(channelId)) {
    let channelCfg = cfg.channels[channelId];
    if (!channelCfg || typeof channelCfg !== 'object' || Array.isArray(channelCfg)) {
      channelCfg = {};
      cfg.channels[channelId] = channelCfg;
    }
    if (typeof channelCfg.enabled !== 'boolean') {
      channelCfg.enabled = true;
    }
  } else {
    delete cfg.channels[channelId];
  }
  delete cfg.plugins.entries[channelId];
}
for (const pluginEntry of extraPluginsById.values()) {
  const pluginId = pluginEntry.pluginId;
  const channelId = pluginEntry.channelId;
  const legacyPluginId = channelId !== pluginId ? channelId : '';
  if (!pluginEntry.loadable) {
    delete cfg.plugins.entries[pluginId];
    if (legacyPluginId) {
      delete cfg.plugins.entries[legacyPluginId];
      delete cfg.channels[pluginId];
    }
    cfg.plugins.allow = cfg.plugins.allow.filter((entryId) => entryId !== pluginId && entryId !== legacyPluginId);
    continue;
  }
  if (legacyPluginId) {
    delete cfg.plugins.entries[legacyPluginId];
    delete cfg.channels[pluginId];
    cfg.plugins.allow = cfg.plugins.allow.filter((entryId) => entryId !== legacyPluginId);
  }
  let entry = cfg.plugins.entries[pluginId];
  if (!entry || typeof entry !== 'object' || Array.isArray(entry)) {
    entry = {};
    cfg.plugins.entries[pluginId] = entry;
  }
  if (typeof entry.enabled !== 'boolean') {
    entry.enabled = true;
  }
  let channelCfg = cfg.channels[channelId];
  if (!channelCfg || typeof channelCfg !== 'object' || Array.isArray(channelCfg)) {
    channelCfg = {};
    cfg.channels[channelId] = channelCfg;
  }
  if (typeof channelCfg.enabled !== 'boolean') {
    channelCfg.enabled = true;
  }
  if (!cfg.plugins.allow.includes(pluginId)) {
    cfg.plugins.allow.push(pluginId);
  }
}
fs.mkdirSync(path.dirname('RUNTIME_CFG'), { recursive: true });
fs.writeFileSync('RUNTIME_CFG', JSON.stringify(cfg, null, 2) + '\\n');
""".replace("RUNTIME_CFG", runtime_cfg)
    cleanup_stale_browser_locks = r"""
const fs = require('fs');
const path = require('path');
const browserRoot = '/home/node/.openclaw/browser';
const staleLockNames = ['SingletonLock', 'SingletonCookie', 'SingletonSocket', 'DevToolsActivePort'];
const safeUnlink = (target) => {
  try {
    fs.rmSync(target, { force: true });
  } catch (error) {
  }
};
const pidIsAlive = (pid) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error && error.code === 'EPERM' ? true : false;
  }
};
try {
  if (fs.existsSync(browserRoot) && fs.statSync(browserRoot).isDirectory()) {
    for (const profileEntry of fs.readdirSync(browserRoot, { withFileTypes: true })) {
      if (!profileEntry.isDirectory()) {
        continue;
      }
      const userDataDir = path.join(browserRoot, profileEntry.name, 'user-data');
      if (!fs.existsSync(userDataDir) || !fs.statSync(userDataDir).isDirectory()) {
        continue;
      }
      const lockPath = path.join(userDataDir, 'SingletonLock');
      let lockStat;
      try {
        lockStat = fs.lstatSync(lockPath);
      } catch (error) {
        continue;
      }
      let lockValue = '';
      try {
        lockValue = lockStat.isSymbolicLink() ? fs.readlinkSync(lockPath) : fs.readFileSync(lockPath, 'utf8');
      } catch (error) {
        lockValue = '';
      }
      const pidMatch = /-(\d+)\s*$/.exec(String(lockValue).trim());
      const stale = !pidMatch || !pidIsAlive(Number.parseInt(pidMatch[1], 10));
      if (!stale) {
        continue;
      }
      for (const name of staleLockNames) {
        safeUnlink(path.join(userDataDir, name));
      }
    }
  }
} catch (error) {
}
"""

    script = (
        f'node -e {shlex.quote(install_compatibility_shims)} || true; '
        f'node -e {shlex.quote(reconcile_channel_plugins)} || true; '
        f'node -e {shlex.quote(cleanup_stale_browser_locks)} || true; '
    )
    if force_responses_store:
        script += (
            f'if [ -f {shlex.quote(target)} ]; then '
            f'grep -q "store: false," {shlex.quote(target)} '
            f"&& sed -i 's/store: false,/store: true,/g' {shlex.quote(target)} || true; "
            "fi; "
            f'if [ -f {shlex.quote(shared)} ]; then '
            f'grep -q "currentBlock.thinkingSignature = JSON.stringify(item);" {shlex.quote(shared)} '
            f"&& sed -i 's|currentBlock.thinkingSignature = JSON.stringify(item);|// stripped by instance-manager to avoid stale rs item replay|g' {shlex.quote(shared)} || true; "
            f'grep -q "currentBlock.textSignature = item.id;" {shlex.quote(shared)} '
            f"&& sed -i 's|currentBlock.textSignature = item.id;|// stripped by instance-manager to avoid msg/rs coupling|g' {shlex.quote(shared)} || true; "
            f'grep -q "let msgId = textBlock.textSignature;" {shlex.quote(shared)} '
            f"&& sed -i 's|let msgId = textBlock.textSignature;|let msgId = undefined; // stripped by instance-manager to avoid msg/rs coupling|g' {shlex.quote(shared)} || true; "
            f'grep -q "if (!msgId)" {shlex.quote(shared)} '
            f"&& sed -i 's|if (!msgId)|if (false \\&\\& !msgId)|g' {shlex.quote(shared)} || true; "
            f'grep -q "else if (msgId.length > 64)" {shlex.quote(shared)} '
            f"&& sed -i 's|else if (msgId.length > 64)|else if (msgId \\&\\& msgId.length > 64)|g' {shlex.quote(shared)} || true; "
            "fi; "
        )
    script += f"exec {start_cmd}"
    return ["sh", "-lc", script]



def ensure_user_runtime(identity, users_root, gateway_token):
    base = os.path.join(users_root, identity)
    data_dir = os.path.join(base, "data")
    config_dir = os.path.join(base, "config")
    runtime_dir = os.path.join(base, "runtime")
    container_uid = int(os.getenv("OPENCLAW_CONTAINER_UID", "1000"))
    container_gid = int(os.getenv("OPENCLAW_CONTAINER_GID", "1000"))
    _safe_mkdir(base, 0o700)
    _safe_mkdir(data_dir, 0o700)
    _safe_mkdir(config_dir, 0o700)
    _safe_mkdir(runtime_dir, 0o700)
    _safe_chown(base, container_uid, container_gid)
    _safe_chown(data_dir, container_uid, container_gid)
    _safe_chown(config_dir, container_uid, container_gid)
    _safe_chown(runtime_dir, container_uid, container_gid)
    _ensure_runtime_config(runtime_dir, container_uid, container_gid, gateway_token=gateway_token)
    _repair_local_device_pairing(runtime_dir, container_uid, container_gid)
    return {
        "data_dir": data_dir,
        "config_dir": config_dir,
        "runtime_dir": runtime_dir,
    }


def ensure_user_artifacts(
    identity, users_root, default_key, default_endpoint, default_model, gateway_token, runtime
):
    resolved_gateway_token = gateway_token.strip()
    base = os.path.join(users_root, identity)
    secrets_dir = os.path.join(base, "secrets")
    container_uid = int(os.getenv("OPENCLAW_CONTAINER_UID", "1000"))
    container_gid = int(os.getenv("OPENCLAW_CONTAINER_GID", "1000"))
    _safe_mkdir(secrets_dir, 0o700)
    _safe_chown(secrets_dir, container_uid, container_gid)

    if not default_key:
        raise RuntimeError("OPENCLAW_DEFAULT_OPENAI_KEY is required for JIT provisioning")

    api_key_file = os.path.join(secrets_dir, "openai_api_key")
    endpoint_file = os.path.join(secrets_dir, "openai_endpoint")
    model_file = os.path.join(secrets_dir, "openai_model")
    _write_if_missing(
        api_key_file,
        default_key,
        0o600,
        container_uid,
        container_gid,
    )
    _write_if_missing(
        endpoint_file,
        default_endpoint,
        0o600,
        container_uid,
        container_gid,
    )
    _write_if_missing(
        model_file,
        default_model,
        0o600,
        container_uid,
        container_gid,
    )
    api_key = _read_secret_file(api_key_file) or default_key
    endpoint = _read_secret_file(endpoint_file) or default_endpoint
    model = _read_secret_file(model_file) or default_model
    return {
        **runtime,
        "secrets_dir": secrets_dir,
        "api_key_file": api_key_file,
        "endpoint_file": endpoint_file,
        "model_file": model_file,
        "api_key": api_key,
        "endpoint": endpoint,
        "model": model,
        "gateway_token": resolved_gateway_token,
    }


def _build_container_spec(identity, artifacts):
    image = os.getenv("OPENCLAW_IMAGE", "").strip()
    if not image:
        raise RuntimeError("OPENCLAW_IMAGE is required for JIT provisioning")
    image_tag = os.getenv("OPENCLAW_IMAGE_TAG", "latest").strip()
    full_image = image if ":" in image else f"{image}:{image_tag}"
    network = os.getenv("OPENCLAW_DOCKER_NETWORK", "infra_default")
    upstream_port = int(os.getenv("OPENCLAW_INSTANCE_PORT", "18789"))
    data_path = os.getenv("OPENCLAW_CONTAINER_DATA_PATH", "/app/data")
    config_path = os.getenv("OPENCLAW_CONTAINER_CONFIG_PATH", "/app/config")
    runtime_path = os.getenv("OPENCLAW_CONTAINER_RUNTIME_PATH", "/home/node/.openclaw")
    cmd_value = os.getenv("OPENCLAW_STARTUP_CMD", "").strip()

    api_key = artifacts.get("api_key") or _read_secret_file(artifacts["api_key_file"])
    endpoint = artifacts.get("endpoint") or _read_secret_file(artifacts["endpoint_file"])
    model = artifacts.get("model") or _read_secret_file(artifacts["model_file"])

    env = [
        f"OPENAI_API_KEY={api_key}",
        f"OPENAI_BASE_URL={endpoint}",
        f"OPENAI_MODEL={model}",
        f"OPENCLAW_OPENAI_ENDPOINT={endpoint}",
        f"OPENCLAW_OPENAI_MODEL={model}",
    ]
    default_channel_plugins = os.getenv("OPENCLAW_DEFAULT_CHANNEL_PLUGINS", "").strip()
    if default_channel_plugins:
        env.append(f"OPENCLAW_DEFAULT_CHANNEL_PLUGINS={default_channel_plugins}")
    default_channel_plugin_dirs = os.getenv("OPENCLAW_DEFAULT_CHANNEL_PLUGIN_DIRS", "").strip()
    if default_channel_plugin_dirs:
        env.append(f"OPENCLAW_DEFAULT_CHANNEL_PLUGIN_DIRS={default_channel_plugin_dirs}")
    gateway_token = (artifacts.get("gateway_token") or "").strip()
    if gateway_token:
        env.append(f"OPENCLAW_GATEWAY_TOKEN={gateway_token}")
        env.append(f"OPENCLAW_GATEWAY_AUTH_TOKEN={gateway_token}")

    for passthrough_name in (
        "OPENCLAW_DASHSCOPE_API_KEY",
        "OPENCLAW_DASHSCOPE_ASR_API_KEY",
        "OPENCLAW_DASHSCOPE_TTS_API_KEY",
        "OPENCLAW_DASHSCOPE_ASR_BASE_URL",
        "OPENCLAW_DASHSCOPE_TTS_BASE_URL",
        "OPENCLAW_DASHSCOPE_ASR_MODEL",
        "OPENCLAW_DASHSCOPE_TTS_MODEL",
        "OPENCLAW_DASHSCOPE_TTS_VOICE",
        "OPENCLAW_AUDIO_OUTPUT_DIR",
    ):
        passthrough_value = os.getenv(passthrough_name, "").strip()
        if passthrough_value:
            env.append(f"{passthrough_name}={passthrough_value}")
    spec = {
        "Image": full_image,
        "Env": env,
        "Labels": {
            "openclaw.managed": "true",
            "openclaw.identity": identity,
            "openclaw.last_active_ts": str(int(time.time())),
            "openclaw.active_sessions": "0",
        },
        "ExposedPorts": {f"{upstream_port}/tcp": {}},
        "HostConfig": {
            "Binds": [
                f'{artifacts["data_dir"]}:{data_path}',
                f'{artifacts["config_dir"]}:{config_path}',
                f'{artifacts["runtime_dir"]}:{runtime_path}',
            ],
            "NetworkMode": network,
            "RestartPolicy": {"Name": "unless-stopped"},
        },
    }
    spec["Cmd"] = _build_default_startup_cmd(
        start_cmd=cmd_value or "node openclaw.mjs gateway --allow-unconfigured",
        force_responses_store=_should_force_openai_responses_store(),
    )
    return spec


def ensure_container_exists(docker, identity, container):
    normalized_identity = normalize_identity(identity)
    users_root = os.getenv("OPENCLAW_USERS_ROOT", "/srv/openclaw/users")
    gateway_token = _ensure_user_gateway_token(
        identity=normalized_identity,
        users_root=users_root,
    )
    runtime = ensure_user_runtime(
        identity=normalized_identity,
        users_root=users_root,
        gateway_token=gateway_token,
    )
    try:
        docker.inspect(container)
        return "existing"
    except DockerAPIError as exc:
        if exc.status != 404:
            raise

    artifacts = ensure_user_artifacts(
        identity=normalized_identity,
        users_root=users_root,
        default_key=os.getenv("OPENCLAW_DEFAULT_OPENAI_KEY", "").strip(),
        default_endpoint=os.getenv("OPENCLAW_DEFAULT_OPENAI_ENDPOINT", "https://api.openai.com/v1"),
        default_model=os.getenv("OPENCLAW_DEFAULT_OPENAI_MODEL", _default_primary_model_ref()),
        gateway_token=gateway_token,
        runtime=runtime,
    )
    spec = _build_container_spec(identity=identity, artifacts=artifacts)
    try:
        docker.create(container, spec)
    except DockerAPIError as exc:
        if exc.status != 409:
            raise
    return "created"


def read_container_runtime_state(docker, container):
    try:
        info = docker.inspect(container)
    except Exception:
        return {"running": None, "health": None}
    running = info.get("running")
    if running is None:
        running = info.get("State", {}).get("Running")
    health = info.get("healthy")
    if health is None:
        health_obj = info.get("State", {}).get("Health")
        health = health_obj.get("Status") if health_obj else None
    return {"running": running, "health": health}


def start_container_if_needed(
    docker,
    container,
    health_timeout_seconds=15,
    wait_for_ready=True,
    runtime_dir=None,
    container_uid=None,
    container_gid=None,
):
    running_ready_seconds = int(os.getenv("OPENCLAW_RUNNING_READY_SECONDS", "20"))

    def schedule_pairing_repair():
        if runtime_dir and container_uid is not None and container_gid is not None:
            pairing_timeout_seconds = max(1, int(health_timeout_seconds))
            _schedule_local_pairing_repair(
                runtime_dir,
                container_uid,
                container_gid,
                timeout_seconds=pairing_timeout_seconds,
            )
            return True
        return False

    state = docker.inspect(container)
    running = state.get("running")
    healthy = state.get("healthy")
    health_status = None
    if running is None:
        running = state.get("State", {}).get("Running", False)
    if healthy is None:
        health_obj = state.get("State", {}).get("Health")
        if health_obj is None:
            # Treat running containers without explicit healthchecks as ready.
            healthy = bool(running)
        else:
            health_status = health_obj.get("Status")
            healthy = health_status == "healthy"

    if running and (healthy or health_status in {None, "starting"}):
        return "running"

    docker.start(container)
    if not wait_for_ready:
        if not schedule_pairing_repair():
            _warm_local_pairing_async(docker, container)
        return "started"

    deadline = time.time() + health_timeout_seconds
    while time.time() < deadline:
        post = docker.inspect(container, wait=True)
        post_healthy = post.get("healthy")
        post_running = post.get("running")
        if post_running is None:
            post_running = post.get("State", {}).get("Running", False)
        post_health_status = None
        if post_healthy is None:
            post_health = post.get("State", {}).get("Health")
            if post_health is None:
                post_healthy = bool(post_running)
            else:
                post_health_status = post_health.get("Status")
                post_healthy = post_health_status == "healthy"
        if post_healthy:
            if not schedule_pairing_repair():
                _warm_local_pairing(docker, container)
            return "started"
        elapsed = health_timeout_seconds - (deadline - time.time())
        if post_running and post_health_status in {"starting", None} and elapsed >= running_ready_seconds:
            if not schedule_pairing_repair():
                _warm_local_pairing(docker, container)
            return "started"
        time.sleep(0.25)

    raise TimeoutError(f"container {container} failed health check")


MAPPING = {}
if os.path.exists("/var/run/docker.sock"):
    DOCKER = DockerAPIClient()
else:
    DOCKER = DockerClient()
THROTTLE = StartupThrottle(max_concurrent=os.getenv("OPENCLAW_STARTUP_MAX_CONCURRENT", "4"))
PROVISION_LOCKS = {}
PROVISION_LOCKS_GUARD = threading.Lock()
CONSOLE_STATIC_ROOT = Path(__file__).resolve().parent / "static" / "console"
HELP_STATIC_ROOT = Path(__file__).resolve().parent / "static" / "help"
CONSOLE_STATIC_FILES = {
    "xterm.js": "application/javascript; charset=utf-8",
    "xterm.css": "text/css; charset=utf-8",
    "xterm-addon-fit.js": "application/javascript; charset=utf-8",
}

HELP_STATIC_FILES = {
    "dashboard-overview.svg": "image/svg+xml; charset=utf-8",
    "console-overview.svg": "image/svg+xml; charset=utf-8",
}


def _acquire_provision_lock(key):
    with PROVISION_LOCKS_GUARD:
        lock = PROVISION_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            PROVISION_LOCKS[key] = lock
    return lock


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def _html(self, status, body):
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _bootstrap_wait_page(self, next_path, detail):
        safe_next = json.dumps(normalize_next_path(next_path))
        safe_detail = json.dumps((detail or "").strip())
        html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <link rel="icon" href="data:," />
    <title>OpenClaw Initializing</title>
    <style>
      body {{
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background: #0f172a;
        color: #e2e8f0;
      }}
      .card {{
        width: min(560px, 92vw);
        border: 1px solid #334155;
        border-radius: 12px;
        padding: 20px;
        background: #111827;
        box-shadow: 0 8px 30px rgba(0, 0, 0, 0.35);
      }}
      .title {{ font-size: 18px; font-weight: 600; margin-bottom: 8px; }}
      .desc {{ opacity: 0.9; margin-bottom: 12px; }}
      .meta {{ font-size: 12px; opacity: 0.75; min-height: 18px; }}
      .spinner {{
        width: 18px; height: 18px; border-radius: 50%;
        border: 2px solid #334155; border-top-color: #22c55e;
        display: inline-block; vertical-align: -3px; margin-right: 8px;
        animation: spin 0.9s linear infinite;
      }}
      @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
    </style>
  </head>
  <body>
    <div class="card">
      <div class="title"><span class="spinner"></span>OpenClaw instance is initializing</div>
      <div class="desc">Your dedicated instance is waking up. This page will redirect automatically once ready.</div>
      <div id="status" class="meta">Checking status...</div>
    </div>
    <script>
      const nextPath = {safe_next};
      const initialDetail = {safe_detail};
      const statusEl = document.getElementById("status");
      const pollMs = 3000;
      let timer = null;
      let retries = 0;
      async function probe() {{
        retries += 1;
        try {{
          const r = await fetch(`/__openclaw__/bootstrap-status?next=${{encodeURIComponent(nextPath)}}&_=${{Date.now()}}`, {{
            credentials: "include",
            cache: "no-store",
            redirect: "follow",
          }});
          if (r.redirected && r.url && r.url.includes("/oauth2/")) {{
            statusEl.textContent = "Authentication expired. Redirecting to sign-in...";
            window.location.assign(r.url);
            return;
          }}
          if (!r.ok) {{
            throw new Error(`status ${{r.status}}`);
          }}
          const payload = await r.json();
          if (payload.ready) {{
            window.location.replace(payload.next || nextPath || "/");
            return;
          }}
          statusEl.textContent = payload.message || initialDetail || "Instance is still starting...";
        }} catch (err) {{
          statusEl.textContent = (initialDetail || "Still waking up") + ` Retry #${{retries}}...`;
        }}
        timer = window.setTimeout(probe, pollMs);
      }}
      probe();
      window.addEventListener("beforeunload", () => timer && clearTimeout(timer));
    </script>
  </body>
</html>"""
        self._html(HTTPStatus.OK, html)

    def _serve_console_asset(self, parsed_path):
        prefix = "/console/assets/"
        asset_name = parsed_path[len(prefix) :]
        if not asset_name or "/" in asset_name or asset_name.startswith("."):
            self._json(HTTPStatus.NOT_FOUND, {"error": "asset not found"})
            return
        content_type = CONSOLE_STATIC_FILES.get(asset_name)
        if content_type is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "asset not found"})
            return
        asset_path = CONSOLE_STATIC_ROOT / asset_name
        try:
            payload = asset_path.read_bytes()
        except FileNotFoundError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "asset missing on server"})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=86400, immutable")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _serve_help_asset(self, parsed_path):
        prefix = "/help/assets/"
        asset_name = parsed_path[len(prefix) :]
        if not asset_name or "/" in asset_name or asset_name.startswith("."):
            self._json(HTTPStatus.NOT_FOUND, {"error": "asset not found"})
            return
        content_type = HELP_STATIC_FILES.get(asset_name)
        if content_type is None:
            self._json(HTTPStatus.NOT_FOUND, {"error": "asset not found"})
            return
        asset_path = HELP_STATIC_ROOT / asset_name
        try:
            payload = asset_path.read_bytes()
        except FileNotFoundError:
            self._json(HTTPStatus.NOT_FOUND, {"error": "asset missing on server"})
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "public, max-age=86400, immutable")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _help_page(self):
        html = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>OpenClaw 使用说明</title>
    <style>
      :root {
        color-scheme: light;
        --bg: #f8fafc;
        --card: #ffffff;
        --line: #dbe3ef;
        --text: #0f172a;
        --muted: #475569;
        --blue: #2563eb;
        --blue-soft: #dbeafe;
        --green-soft: #dcfce7;
      }
      * { box-sizing: border-box; }
      html { scroll-behavior: smooth; }
      body {
        margin: 0;
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
        color: var(--text);
        background: linear-gradient(180deg, #eff6ff 0%, var(--bg) 220px);
      }
      .topbar {
        position: sticky;
        top: 0;
        z-index: 20;
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 12px;
        padding: 14px 22px;
        background: rgba(255, 255, 255, 0.94);
        border-bottom: 1px solid var(--line);
        backdrop-filter: blur(8px);
      }
      .brand { font-size: 18px; font-weight: 700; }
      .nav { display: flex; flex-wrap: wrap; gap: 10px; }
      .nav a, .nav button {
        border: 1px solid var(--line);
        background: white;
        color: var(--text);
        text-decoration: none;
        border-radius: 999px;
        padding: 10px 16px;
        font-size: 14px;
        cursor: pointer;
      }
      .nav button.primary { background: var(--blue); color: white; border-color: var(--blue); }
      .shell { width: min(1120px, calc(100vw - 32px)); margin: 0 auto; padding: 28px 0 56px; }
      .hero, .section, .tip {
        background: var(--card);
        border: 1px solid var(--line);
        border-radius: 22px;
        box-shadow: 0 10px 30px rgba(15, 23, 42, 0.06);
      }
      .hero { padding: 28px; }
      .hero h1 { margin: 0 0 12px; font-size: 34px; }
      .hero p { margin: 0; line-height: 1.7; color: var(--muted); font-size: 17px; }
      .hero .chips { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 18px; }
      .chip {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        background: var(--blue-soft);
        color: #1d4ed8;
        padding: 10px 14px;
        border-radius: 999px;
        font-size: 14px;
        font-weight: 600;
      }
      .grid { display: grid; gap: 18px; margin-top: 18px; }
      .grid.two { grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
      .section { padding: 24px; }
      .section h2 { margin: 0 0 14px; font-size: 24px; }
      .section h3 { margin: 0 0 8px; font-size: 18px; }
      .section p, .section li { color: var(--muted); line-height: 1.7; }
      .steps { padding-left: 20px; margin: 0; }
      .commands {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
        gap: 12px;
        margin-top: 16px;
      }
      .command {
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 14px;
        background: #f8fafc;
      }
      .command code {
        display: inline-block;
        font-size: 16px;
        font-weight: 700;
        color: #1d4ed8;
        background: transparent;
      }
      figure {
        margin: 0;
        border: 1px solid var(--line);
        border-radius: 20px;
        overflow: hidden;
        background: #fff;
      }
      figure img { display: block; width: 100%; height: auto; }
      figcaption { padding: 14px 16px 18px; color: var(--muted); font-size: 14px; }
      .tip {
        margin-top: 18px;
        padding: 18px 20px;
        background: var(--green-soft);
      }
      .tip strong { display: block; margin-bottom: 6px; }
      @media (max-width: 720px) {
        .hero h1 { font-size: 28px; }
        .topbar { padding: 12px 14px; }
        .shell { width: min(100vw - 20px, 1120px); padding-top: 18px; }
      }
    </style>
  </head>
  <body>
    <div class="topbar">
      <div class="brand">OpenClaw 帮助页</div>
      <div class="nav">
        <a href="/help">帮助页</a>
        <button class="primary" type="button" onclick="openManaged('/', 'openclaw-dashboard')">登录 Dashboard</button>
        <button type="button" onclick="openManaged('/console', 'openclaw-console')">控制台</button>
      </div>
    </div>
    <main class="shell">
      <section class="hero">
        <h1>OpenClaw 使用说明</h1>
        <p>这是给第一次使用 OpenClaw 的同事准备的快速帮助页。你可以先登录进入 Dashboard，第一次初始化大约需要 2 分钟；进入聊天后，直接发送“你好”就可以开始对话。</p>
        <div class="chips">
          <span class="chip">1. 使用 Yinxiang SSO 登录</span>
          <span class="chip">2. 首次初始化大约需要 2 分钟</span>
          <span class="chip">3. 进入聊天后发送“你好”开始</span>
        </div>
      </section>
      <div class="grid two">
        <section class="section">
          <h2>快速开始</h2>
          <ol class="steps">
            <li>点击上方 <strong>登录 Dashboard</strong>，使用 Yinxiang SSO 登录。</li>
            <li>如果是第一次进入，系统会自动为你准备专属环境，请耐心等待约 2 分钟。</li>
            <li>进入聊天界面后，先发一句“你好”，确认对话已经正常开始。</li>
            <li>之后就可以像和同事聊天一样，直接提出问题或交代任务。</li>
          </ol>
          <div class="tip">
            <strong>小提示</strong>
            如果刚登录时看到等待页面，这是正常现象；页面会在环境准备好后自动进入系统。
          </div>
        </section>
        <figure>
          <img src="/help/assets/dashboard-overview.svg" alt="Dashboard 界面示意图" />
          <figcaption>Dashboard 主要用于聊天、查看会话和继续追问。第一次进入后，先发送“你好”最稳妥。</figcaption>
        </figure>
      </div>
      <section class="section" style="margin-top: 18px;">
        <h2>常用命令</h2>
        <p>下面这些命令最常用，建议先记住。它们可以直接在聊天输入框里发送，也可以在需要时让 OpenClaw 帮你解释。</p>
        <div class="commands">
          <div class="command"><code>/help</code><p>打开帮助菜单，查看常见入口和说明。</p></div>
          <div class="command"><code>/models</code> / <code>/model</code><p>查看或切换当前可用模型。</p></div>
          <div class="command"><code>/channels</code><p>查看和管理企微、Telegram 等 IM 渠道。</p></div>
          <div class="command"><code>/status</code><p>查看当前环境、Gateway 和浏览器等状态。</p></div>
          <div class="command"><code>/thinking</code><p>查看或调整思考模式相关设置。</p></div>
          <div class="command"><code>/reasoning</code><p>查看或调整推理模式相关设置。</p></div>
          <div class="command"><code>/skill</code><p>查看可用技能，或让系统按技能方式完成任务。</p></div>
        </div>
      </section>
      <div class="grid two">
        <figure>
          <img src="/help/assets/console-overview.svg" alt="控制台界面示意图" />
          <figcaption>控制台适合查看状态、执行命令和排查问题；如果你不熟悉命令行，也可以先让 OpenClaw 告诉你要执行什么。</figcaption>
        </figure>
        <section class="section">
          <h2>控制台是做什么的？</h2>
          <p><strong>控制台（/console）</strong> 是你的专属终端窗口。适合做这些事情：</p>
          <ul class="steps">
            <li>查看当前系统状态，例如 `openclaw status`。</li>
            <li>执行简单命令，检查文件、日志或网络状态。</li>
            <li>在 OpenClaw 提示你需要进一步排查时，配合它一起处理问题。</li>
          </ul>
          <div class="tip">
            <strong>如果你不熟悉命令行也没关系</strong>
            可以直接在聊天里描述问题，让 OpenClaw 告诉你下一步该做什么。
          </div>
        </section>
      </div>
      <section class="section" style="margin-top: 18px;">
        <h2>配置企微、Telegram 等 IM</h2>
        <p>如果你希望通过企微、Telegram 等 IM 和 OpenClaw 对话，可以先在聊天中使用 <code>/channels</code> 查看当前渠道状态，再按提示完成配置。</p>
        <ul class="steps">
          <li>企微：适合公司内部使用，配置完成后可直接在企微里发消息给 OpenClaw。</li>
          <li>Telegram：适合个人或跨设备使用，配置完成后可通过机器人聊天。</li>
          <li>其他 IM：也可以在 <code>/channels</code> 里查看是否已启用，以及是否还需要补充配置。</li>
        </ul>
      </section>
    </main>
    <script>
      function openManaged(url, targetName) {
        const nextWindow = window.open(url, targetName);
        if (nextWindow && typeof nextWindow.focus === 'function') {
          nextWindow.focus();
          return;
        }
        window.location.assign(url);
      }
    </script>
  </body>
</html>"""
        self._html(HTTPStatus.OK, html)

    def _console_page(self):
        html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>OpenClaw Console</title>
    <link rel="stylesheet" href="/console/assets/xterm.css" />
    <style>
      html, body { margin: 0; width: 100%; height: 100%; background: #0b1020; color: #e5e7eb; }
      body { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
      #terminal { width: 100%; height: 100%; padding-top: 34px; box-sizing: border-box; }
      .banner {
        position: fixed; top: 8px; left: 8px; z-index: 10; font-size: 12px;
        background: rgba(15, 23, 42, 0.82); border: 1px solid #334155; border-radius: 6px;
        padding: 4px 8px; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      }
      .hint {
        position: fixed; top: 8px; right: 8px; z-index: 10; font-size: 12px;
        background: rgba(15, 23, 42, 0.82); border: 1px solid #334155; border-radius: 6px;
        padding: 4px 8px; color: #94a3b8;
      }
    </style>
  </head>
  <body>
    <div class="banner">OpenClaw Container Console</div>
    <div class="hint">Auto-fit enabled | Ctrl+C to interrupt</div>
    <div id="terminal"></div>
    <script src="/console/assets/xterm.js"></script>
    <script src="/console/assets/xterm-addon-fit.js"></script>
    <script>
      if (typeof window.Terminal !== "function" || !window.FitAddon || typeof window.FitAddon.FitAddon !== "function") {
        document.body.innerHTML = "<pre style='padding:12px'>[console error] failed to load xterm assets</pre>";
      } else {
        const encoder = new TextEncoder();
        const decoder = new TextDecoder();
        const wsProto = window.location.protocol === "https:" ? "wss" : "ws";
        const ws = new WebSocket(`${wsProto}://${window.location.host}/console/ws`);
        ws.binaryType = "arraybuffer";
        const term = new Terminal({
          cursorBlink: true,
          scrollback: 5000,
          fontSize: 13,
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace",
          theme: {
            background: "#0b1020",
            foreground: "#e5e7eb",
            cursor: "#93c5fd",
            selectionBackground: "#1e293b",
          },
        });
        const fitAddon = new window.FitAddon.FitAddon();
        term.loadAddon(fitAddon);
        term.open(document.getElementById("terminal"));
        term.focus();
        fitAddon.fit();
        term.writeln("\\u001b[1;34mOpenClaw Container Console\\u001b[0m");
        term.writeln("\\u001b[90mConnecting...\\u001b[0m");

        let resizeTimer = null;
        function notifyResize() {
          if (ws.readyState !== WebSocket.OPEN) return;
          ws.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
        }
        function fitAndNotify() {
          fitAddon.fit();
          notifyResize();
        }
        function scheduleFit() {
          if (resizeTimer) window.clearTimeout(resizeTimer);
          resizeTimer = window.setTimeout(fitAndNotify, 80);
        }

        function writeChunk(data) {
          if (typeof data === "string") {
            term.write(data);
            return;
          }
          term.write(decoder.decode(new Uint8Array(data), { stream: true }));
        }

        ws.onopen = () => {
          term.writeln("\\u001b[32m[connected]\\u001b[0m");
          fitAndNotify();
        };
        ws.onclose = () => term.writeln("\\r\\n\\u001b[33m[disconnected]\\u001b[0m");
        ws.onerror = () => term.writeln("\\r\\n\\u001b[31m[console websocket error]\\u001b[0m");
        ws.onmessage = (ev) => writeChunk(ev.data);
        term.onData((data) => {
          if (ws.readyState === WebSocket.OPEN) ws.send(encoder.encode(data));
        });
        window.addEventListener("paste", (e) => {
          const text = e.clipboardData && e.clipboardData.getData("text");
          if (!text) return;
          e.preventDefault();
          if (ws.readyState === WebSocket.OPEN) ws.send(encoder.encode(text));
        });
        window.addEventListener("click", () => term.focus());
        window.addEventListener("resize", () => {
          term.focus();
          scheduleFit();
        });
        if (typeof window.ResizeObserver === "function") {
          const containerEl = document.getElementById("terminal");
          const observer = new ResizeObserver(() => scheduleFit());
          observer.observe(containerEl);
        }
      }
    </script>
  </body>
</html>"""
        self._html(HTTPStatus.OK, html)

    def _should_use_bootstrap_wait_page(self, parsed_path):
        if parsed_path in {"/health", "/__openclaw__/bootstrap-status"}:
            return False
        if is_websocket_upgrade(self.headers):
            return False
        return is_browser_navigation_request(self.command, self.headers)

    def _open_console_exec_stream(self, container):
        preferred_shell = os.getenv("OPENCLAW_CONSOLE_SHELL", "/bin/bash -il").strip() or "/bin/sh -i"
        fallback_shell = os.getenv("OPENCLAW_CONSOLE_FALLBACK_SHELL", "/bin/sh -i").strip() or "/bin/sh -i"
        shell_candidates = []
        for raw in (preferred_shell, fallback_shell):
            try:
                parts = shlex.split(raw)
            except ValueError:
                parts = []
            if parts and parts not in shell_candidates:
                shell_candidates.append(parts)

        last_error = None
        for cmd in shell_candidates:
            try:
                created = DOCKER.create_exec(container, cmd, user="node", tty=True)
                exec_id = created.get("Id") if isinstance(created, dict) else None
                if not exec_id:
                    raise RuntimeError("missing exec id")
                sock, prebuffer = DOCKER.start_exec_stream(exec_id, tty=True)
                return sock, prebuffer, cmd, exec_id
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"failed to open console exec stream: {last_error}")

    def _proxy_console_websocket(self, container):
        if not is_websocket_upgrade(self.headers):
            self.send_response(HTTPStatus.UPGRADE_REQUIRED)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "websocket upgrade required"}).encode("utf-8"))
            return

        sec_key = (self.headers.get("Sec-WebSocket-Key") or "").strip()
        if not sec_key:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "missing websocket key"})
            return

        request_id = self.headers.get("X-Request-Id") or self.headers.get("X-Amzn-Trace-Id")
        exec_sock = None
        prebuffer = b""
        cmd = []
        exec_id = None
        try:
            exec_sock, prebuffer, cmd, exec_id = self._open_console_exec_stream(container)
            accept = _websocket_accept_key(sec_key)
            self.send_response(HTTPStatus.SWITCHING_PROTOCOLS)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()

            emit_identity_audit(
                "console_ws_connected",
                request_id=request_id,
                container=container,
                path=self.path,
                shell_command=" ".join(cmd),
            )

            client = self.connection
            if prebuffer:
                _ws_send_frame(client, prebuffer, opcode=2)

            sockets = [client, exec_sock]
            while True:
                readable, _, errored = select.select(sockets, [], sockets, 60)
                if errored:
                    break
                if not readable:
                    continue
                for sock in readable:
                    if sock is client:
                        opcode, payload = _ws_read_frame(client)
                        if opcode == 8:
                            _ws_send_frame(client, b"", opcode=8)
                            return
                        if opcode == 9:
                            _ws_send_frame(client, payload, opcode=10)
                            continue
                        if opcode not in {1, 2}:
                            continue
                        if opcode == 1:
                            ctrl = _parse_console_control(payload)
                            if ctrl and ctrl.get("type") == "resize" and exec_id:
                                try:
                                    DOCKER.resize_exec(exec_id, ctrl["cols"], ctrl["rows"])
                                except Exception:
                                    pass
                                continue
                        if payload:
                            exec_sock.sendall(payload)
                    else:
                        data = exec_sock.recv(65536)
                        if not data:
                            return
                        _ws_send_frame(client, data, opcode=2)
        except Exception as exc:
            emit_identity_audit(
                "console_ws_error",
                request_id=request_id,
                container=container,
                path=self.path,
                error=str(exc),
            )
            try:
                if is_websocket_upgrade(self.headers):
                    _ws_send_frame(self.connection, f"\r\n[console error] {exc}\r\n", opcode=1)
            except Exception:
                pass
            if not is_websocket_upgrade(self.headers):
                raise
        finally:
            if exec_sock is not None:
                try:
                    exec_sock.close()
                except OSError:
                    pass

    def _handle_bootstrap_status(self, parsed):
        query = parse_qs(parsed.query)
        next_path = normalize_next_path(query.get("next", ["/"])[0])
        message = "OpenClaw instance is waking up..."
        try:
            # Keep status probe fast: ensure/start container but do not block on health.
            container = self._resolve_target_container(wait_for_ready=False)
        except ValueError:
            self._json(HTTPStatus.OK, {"ready": False, "next": next_path, "message": "Missing identity"})
            return
        except PermissionError:
            self._json(HTTPStatus.OK, {"ready": False, "next": next_path, "message": "Identity not allowed"})
            return
        except RuntimeError:
            self._json(
                HTTPStatus.OK,
                {"ready": False, "next": next_path, "message": "Instance startup is throttled, retrying..."},
            )
            return
        except TimeoutError:
            self._json(
                HTTPStatus.OK,
                {"ready": False, "next": next_path, "message": "Instance startup timeout, retrying..."},
            )
            return

        ready = is_upstream_ready(container)
        if ready:
            message = "Instance is ready, redirecting..."
        self._json(HTTPStatus.OK, {"ready": ready, "next": next_path, "message": message})

    def _resolve_target_container(self, query=None, wait_for_ready=True):
        query = query or {}
        request_id = self.headers.get("X-Request-Id") or self.headers.get("X-Amzn-Trace-Id")
        header_identity, header_sub = extract_identity(self.headers)
        employee_id = header_identity
        user_sub = header_sub
        if should_allow_loopback_query_identity(self.client_address, header_identity, header_sub):
            employee_id = query.get("employee_id", [None])[0] or header_identity
            user_sub = query.get("user_sub", [None])[0] or header_sub
        raw_identity = employee_id or user_sub
        identity = normalize_identity(raw_identity)
        allowed_domains = split_csv_values(os.getenv("OPENCLAW_ALLOWED_EMAIL_DOMAINS", ""))
        allowed_groups = split_csv_values(os.getenv("OPENCLAW_ALLOWED_GROUPS", ""))
        if not is_identity_allowed(self.headers, allowed_domains, allowed_groups):
            emit_identity_audit(
                "identity_denied",
                request_id=request_id,
                raw_identity=raw_identity,
                normalized_identity=identity,
                reason="not_allowed",
            )
            raise PermissionError("identity not allowed")

        container = resolve_container_name(employee_id, user_sub, MAPPING)
        provision_state = "existing"
        if os.getenv("OPENCLAW_JIT_PROVISION", "true").lower() in {"1", "true", "yes"}:
            lock = _acquire_provision_lock(container)
            with lock:
                provision_state = ensure_container_exists(DOCKER, identity=identity, container=container)

        if not THROTTLE.try_acquire():
            raise RuntimeError("startup throttled")

        try:
            timeout = int(os.getenv("OPENCLAW_HEALTH_TIMEOUT_SECONDS", "120"))
            users_root = os.getenv("OPENCLAW_USERS_ROOT", "/srv/openclaw/users")
            runtime_dir = os.path.join(users_root, identity, "runtime")
            container_uid = int(os.getenv("OPENCLAW_CONTAINER_UID", "1000"))
            container_gid = int(os.getenv("OPENCLAW_CONTAINER_GID", "1000"))
            startup_state = start_container_if_needed(
                DOCKER,
                container,
                health_timeout_seconds=timeout,
                wait_for_ready=wait_for_ready,
                runtime_dir=runtime_dir,
                container_uid=container_uid,
                container_gid=container_gid,
            )
        finally:
            THROTTLE.release()

        try:
            _write_last_active_marker(
                identity=identity,
                users_root=os.getenv("OPENCLAW_USERS_ROOT", "/srv/openclaw/users"),
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "event": "last_active_update_error",
                        "container": container,
                        "normalized_identity": identity,
                        "error": str(exc),
                    },
                    ensure_ascii=True,
                ),
                flush=True,
            )

        lifecycle = classify_instance_lifecycle(provision_state, startup_state)
        try:
            _repair_local_device_pairing(runtime_dir, container_uid, container_gid)
        except OSError:
            pass
        runtime = read_container_runtime_state(DOCKER, container)
        emit_identity_audit(
            "identity_routed",
            request_id=request_id,
            raw_identity=raw_identity,
            normalized_identity=identity,
            container=container,
            provision_state=provision_state,
            startup_state=startup_state,
            lifecycle=lifecycle,
            container_running=runtime.get("running"),
            container_health=runtime.get("health"),
        )

        return container

    def _proxy_websocket(self, container):
        upstream_port = int(os.getenv("OPENCLAW_INSTANCE_PORT", "18789"))
        upstream = socket.create_connection((container, upstream_port), timeout=30)
        client = self.connection
        handshake_established = False

        try:
            request_id = self.headers.get("X-Request-Id") or self.headers.get("X-Amzn-Trace-Id")
            trusted_header = _trusted_proxy_user_header_name()
            has_trusted_header = bool(
                self.headers.get(trusted_header)
                if trusted_header == "host"
                else any(
                    key.lower() == trusted_header
                    and isinstance(self.headers.get(key), str)
                    and self.headers.get(key).strip()
                    for key in self.headers.keys()
                )
            )
            emit_identity_audit(
                "ws_proxy_handshake",
                request_id=request_id,
                container=container,
                path=self.path,
                trusted_user_header=trusted_header,
                trusted_user_present=has_trusted_header,
                has_upgrade_header=bool((self.headers.get("Upgrade") or "").strip()),
                has_ws_key=bool((self.headers.get("Sec-WebSocket-Key") or "").strip()),
            )
            request_lines = [f"{self.command} {self.path} HTTP/1.1"]
            has_host = False
            upstream_headers = dict(self.headers.items())
            _inject_trusted_proxy_user_header_if_needed(upstream_headers)
            for key, value in self.headers.items():
                if key.lower() == "host":
                    has_host = True
                    request_lines.append(f"Host: {container}:{upstream_port}")
                elif key.lower() in {"connection", "upgrade", "sec-websocket-key", "sec-websocket-version", "sec-websocket-extensions", "sec-websocket-protocol", "origin", "cookie", "authorization", "x-forwarded-user", "x-forwarded-email", "x-auth-request-user", "x-auth-request-email", "x-request-id", "x-amzn-trace-id"}:
                    # keep original iteration order for handshake-critical headers
                    request_lines.append(f"{key}: {value}")
                elif key in upstream_headers:
                    request_lines.append(f"{key}: {upstream_headers.pop(key)}")
            # add any injected header that was absent in incoming handshake
            for key, value in upstream_headers.items():
                if key.lower() == "host":
                    continue
                if any(existing.split(":", 1)[0].strip().lower() == key.lower() for existing in request_lines[1:]):
                    continue
                request_lines.append(f"{key}: {value}")
            if not has_host:
                request_lines.append(f"Host: {container}:{upstream_port}")
            raw_request = ("\r\n".join(request_lines) + "\r\n\r\n").encode("utf-8")
            upstream.sendall(raw_request)

            response = b""
            while b"\r\n\r\n" not in response:
                chunk = upstream.recv(4096)
                if not chunk:
                    raise ConnectionError("upstream closed before websocket headers")
                response += chunk
                if len(response) > 65536:
                    raise ConnectionError("upstream websocket headers too large")

            head, tail = response.split(b"\r\n\r\n", 1)
            client.sendall(head + b"\r\n\r\n")
            if tail:
                client.sendall(tail)

            status_line = head.split(b"\r\n", 1)[0].decode("utf-8", errors="ignore")
            status_code = 0
            parts = status_line.split(" ")
            if len(parts) >= 2 and parts[1].isdigit():
                status_code = int(parts[1])
            if status_code != 101:
                emit_identity_audit(
                    "ws_proxy_non_101",
                    request_id=request_id,
                    container=container,
                    path=self.path,
                    status_line=status_line,
                )
                return

            handshake_established = True
            sockets = [client, upstream]
            while True:
                readable, _, errored = select.select(sockets, [], sockets, 60)
                if errored:
                    return
                if not readable:
                    continue
                for sock in readable:
                    try:
                        data = sock.recv(65536)
                    except BlockingIOError:
                        continue
                    except OSError:
                        return
                    if not data:
                        return
                    target = upstream if sock is client else client
                    try:
                        target.sendall(data)
                    except BlockingIOError:
                        continue
        except Exception as exc:
            request_id = self.headers.get("X-Request-Id") or self.headers.get("X-Amzn-Trace-Id")
            emit_identity_audit(
                "ws_proxy_error",
                request_id=request_id,
                container=container,
                path=self.path,
                handshake_established=handshake_established,
                error=str(exc),
            )
            if not handshake_established:
                raise
            return
        finally:
            try:
                upstream.close()
            except OSError:
                pass

    def _proxy_request(self, container):
        upstream_port = int(os.getenv("OPENCLAW_INSTANCE_PORT", "18789"))
        if is_websocket_upgrade(self.headers):
            self._proxy_websocket(container)
            return
        method = self.command
        body = None
        if method in ("POST", "PUT", "PATCH"):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else None

        conn = http.client.HTTPConnection(container, upstream_port, timeout=30)
        upstream_headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in {"host", "connection", "content-length"}
        }
        _inject_trusted_proxy_user_header_if_needed(upstream_headers)
        conn.request(method, self.path, body=body, headers=upstream_headers)
        upstream_resp = conn.getresponse()
        upstream_body = upstream_resp.read()

        self.send_response(upstream_resp.status)
        passthrough = {"content-type", "set-cookie", "cache-control", "location"}
        for key, value in upstream_resp.getheaders():
            if key.lower() in passthrough:
                self.send_header(key, value)
        self.end_headers()
        if upstream_body:
            self.wfile.write(upstream_body)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return

        if parsed.path.startswith("/help/assets/"):
            self._serve_help_asset(parsed.path)
            return

        if parsed.path in ("/help", "/help/"):
            self._help_page()
            return

        if parsed.path.startswith("/console/assets/"):
            self._serve_console_asset(parsed.path)
            return

        if parsed.path in ("/console", "/console/"):
            try:
                self._resolve_target_container()
            except ValueError:
                if self._should_use_bootstrap_wait_page(parsed.path):
                    self._bootstrap_wait_page(self.path, "Waiting for authenticated identity...")
                else:
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "missing identity"})
                return
            except PermissionError:
                self._json(HTTPStatus.FORBIDDEN, {"error": "identity not allowed"})
                return
            except RuntimeError:
                if self._should_use_bootstrap_wait_page(parsed.path):
                    self._bootstrap_wait_page(self.path, "OpenClaw instance is starting...")
                else:
                    self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "startup throttled"})
                return
            except TimeoutError:
                if self._should_use_bootstrap_wait_page(parsed.path):
                    self._bootstrap_wait_page(self.path, "OpenClaw instance startup timed out. Retrying...")
                else:
                    self._json(HTTPStatus.GATEWAY_TIMEOUT, {"error": "container startup timeout"})
                return
            self._console_page()
            return

        if parsed.path == "/console/ws":
            try:
                container = self._resolve_target_container()
            except ValueError:
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "missing identity"})
                return
            except PermissionError:
                self._json(HTTPStatus.FORBIDDEN, {"error": "identity not allowed"})
                return
            except RuntimeError:
                self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "startup throttled"})
                return
            except TimeoutError:
                self._json(HTTPStatus.GATEWAY_TIMEOUT, {"error": "container startup timeout"})
                return
            self._proxy_console_websocket(container)
            return

        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Cache-Control", "public, max-age=86400, immutable")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if parsed.path == "/__openclaw__/bootstrap-status":
            self._handle_bootstrap_status(parsed)
            return

        if parsed.path == "/resolve":
            query = parse_qs(parsed.query)

            try:
                container = self._resolve_target_container(query=query)
            except ValueError:
                if self._should_use_bootstrap_wait_page(parsed.path):
                    self._bootstrap_wait_page(self.path, "Waiting for authenticated identity...")
                else:
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "missing identity"})
                return
            except PermissionError:
                self._json(HTTPStatus.FORBIDDEN, {"error": "identity not allowed"})
                return
            except RuntimeError:
                if self._should_use_bootstrap_wait_page(parsed.path):
                    self._bootstrap_wait_page(self.path, "OpenClaw instance is starting...")
                else:
                    self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "startup throttled"})
                return
            except TimeoutError:
                if self._should_use_bootstrap_wait_page(parsed.path):
                    self._bootstrap_wait_page(self.path, "OpenClaw instance startup timed out. Retrying...")
                else:
                    self._json(HTTPStatus.GATEWAY_TIMEOUT, {"error": "container startup timeout"})
                return
            self._json(HTTPStatus.OK, {"container": container, "state": "ready"})
            return

        prefer_wait_page = self._should_use_bootstrap_wait_page(parsed.path)
        try:
            # For browser navigation requests, avoid blocking the initial response
            # and render the bootstrap page while container health converges.
            container = self._resolve_target_container(wait_for_ready=not prefer_wait_page)
        except ValueError:
            if self._should_use_bootstrap_wait_page(parsed.path):
                self._bootstrap_wait_page(self.path, "Waiting for authenticated identity...")
            else:
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "missing identity"})
            return
        except PermissionError:
            self._json(HTTPStatus.FORBIDDEN, {"error": "identity not allowed"})
            return
        except RuntimeError:
            if self._should_use_bootstrap_wait_page(parsed.path):
                self._bootstrap_wait_page(self.path, "OpenClaw instance is starting...")
            else:
                self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "startup throttled"})
            return
        except TimeoutError:
            if self._should_use_bootstrap_wait_page(parsed.path):
                self._bootstrap_wait_page(self.path, "OpenClaw instance startup timed out. Retrying...")
            else:
                self._json(HTTPStatus.GATEWAY_TIMEOUT, {"error": "container startup timeout"})
            return

        if not re.match(r"^[a-zA-Z0-9._-]+$", container):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid container name"})
            return

        if prefer_wait_page and not is_upstream_ready(container):
            self._bootstrap_wait_page(self.path, "OpenClaw instance is waking up...")
            return

        try:
            self._proxy_request(container)
        except Exception as exc:
            if self._should_use_bootstrap_wait_page(parsed.path) and is_retryable_upstream_error(exc):
                self._bootstrap_wait_page(self.path, str(exc))
                return
            self._json(HTTPStatus.BAD_GATEWAY, {"error": str(exc)})

    def do_POST(self):
        self.do_GET()

    def do_PUT(self):
        self.do_GET()

    def do_PATCH(self):
        self.do_GET()

    def do_DELETE(self):
        self.do_GET()


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()
