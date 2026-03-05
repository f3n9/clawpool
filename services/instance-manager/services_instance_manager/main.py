#!/usr/bin/env python3
import json
import http.client
import os
import re
import secrets
import select
import shlex
import socket
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
        "openclaw status >/dev/null 2>&1 || true; openclaw devices approve --latest >/dev/null 2>&1 || true",
    ]
    timeout_seconds = int(os.getenv("OPENCLAW_LOCAL_PAIRING_WARMUP_TIMEOUT_SECONDS", "20"))
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

    # Ensure commonly used channel plugins are enabled by default so Channels page can
    # resolve per-channel config schema (users can still override later in runtime config).
    plugins = cfg.get("plugins")
    if not isinstance(plugins, dict):
        plugins = {}
        cfg["plugins"] = plugins
    entries = plugins.get("entries")
    if not isinstance(entries, dict):
        entries = {}
        plugins["entries"] = entries
    default_channel_plugins = split_csv_values(
        os.getenv("OPENCLAW_DEFAULT_CHANNEL_PLUGINS", "telegram,googlechat")
    )
    for plugin_id in default_channel_plugins:
        if not re.match(r"^[a-z0-9._-]+$", plugin_id):
            continue
        entry = entries.get(plugin_id)
        if not isinstance(entry, dict):
            entry = {}
            entries[plugin_id] = entry
        if not isinstance(entry.get("enabled"), bool):
            entry["enabled"] = True

    # Ensure the default agent model is OpenAI-based so users don't fall back to image defaults
    # such as anthropic/claude-opus-* when no anthropic auth is configured.
    desired_model = os.getenv("OPENCLAW_DEFAULT_OPENAI_MODEL", "gpt-5.2").strip() or "gpt-5.2"
    if "/" not in desired_model:
        desired_model = f"openai/{desired_model}"
    allowed_models = split_csv_values(os.getenv("OPENCLAW_ALLOWED_MODELS", ""))
    allowed_models = [m if "/" in m else f"openai/{m}" for m in allowed_models]

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
    should_set_primary = not isinstance(primary, str) or not primary.strip()
    if not should_set_primary and isinstance(primary, str):
        if primary.startswith("anthropic/"):
            should_set_primary = True
        elif allowed_models and primary not in allowed_models:
            should_set_primary = True
    if should_set_primary:
        model_cfg["primary"] = desired_model
        primary = desired_model

    models_cfg = defaults.get("models")
    if not isinstance(models_cfg, dict):
        models_cfg = {}
        defaults["models"] = models_cfg
    if isinstance(primary, str) and primary and primary not in models_cfg:
        models_cfg[primary] = {}

    normalized_openai_model_ids = []
    for model_ref in allowed_models:
        if not isinstance(model_ref, str) or not model_ref.strip():
            continue
        ref = model_ref.strip()
        if "/" in ref:
            provider, model_id = ref.split("/", 1)
            if provider.strip().lower() != "openai":
                continue
            candidate = model_id.strip()
        else:
            candidate = ref
        if candidate and candidate not in normalized_openai_model_ids:
            normalized_openai_model_ids.append(candidate)
    if isinstance(primary, str) and primary.startswith("openai/"):
        primary_model_id = primary.split("/", 1)[1].strip()
        if primary_model_id and primary_model_id not in normalized_openai_model_ids:
            normalized_openai_model_ids.insert(0, primary_model_id)
    if not normalized_openai_model_ids:
        normalized_openai_model_ids = [desired_model.split("/", 1)[1]]

    # Force OpenAI requests over HTTP/SSE via configured endpoint.
    # This avoids hardcoded direct OpenAI WebSocket routing when using gateway HTTP proxy endpoints.
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
    openai_provider["api"] = "openai-responses"
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
        for model_id in normalized_openai_model_ids
    ]
    providers["openai"] = openai_provider

    for model_id in normalized_openai_model_ids:
        model_ref = f"openai/{model_id}"
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


def ensure_user_runtime(identity, users_root, gateway_token=""):
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
    identity, users_root, default_key, default_endpoint, default_model, gateway_token=""
):
    resolved_gateway_token = (gateway_token or _ensure_user_gateway_token(identity, users_root)).strip()
    runtime = ensure_user_runtime(identity, users_root, gateway_token=resolved_gateway_token)
    base = os.path.join(users_root, identity)
    secrets_dir = os.path.join(base, "secrets")
    container_uid = int(os.getenv("OPENCLAW_CONTAINER_UID", "1000"))
    container_gid = int(os.getenv("OPENCLAW_CONTAINER_GID", "1000"))
    _safe_mkdir(secrets_dir, 0o700)
    _safe_chown(secrets_dir, container_uid, container_gid)

    if not default_key:
        raise RuntimeError("OPENCLAW_DEFAULT_OPENAI_KEY is required for JIT provisioning")

    _write_if_missing(
        os.path.join(secrets_dir, "openai_api_key"),
        default_key,
        0o600,
        container_uid,
        container_gid,
    )
    _write_if_missing(
        os.path.join(secrets_dir, "openai_endpoint"),
        default_endpoint,
        0o600,
        container_uid,
        container_gid,
    )
    _write_if_missing(
        os.path.join(secrets_dir, "openai_model"),
        default_model,
        0o600,
        container_uid,
        container_gid,
    )
    return {
        **runtime,
        "secrets_dir": secrets_dir,
        "api_key_file": os.path.join(secrets_dir, "openai_api_key"),
        "endpoint_file": os.path.join(secrets_dir, "openai_endpoint"),
        "model_file": os.path.join(secrets_dir, "openai_model"),
        "gateway_token": resolved_gateway_token,
    }


def _build_container_spec(container, identity, artifacts):
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

    api_key = _read_secret_file(artifacts["api_key_file"])
    endpoint = _read_secret_file(artifacts["endpoint_file"])
    model = _read_secret_file(artifacts["model_file"])

    env = [
        f"OPENAI_API_KEY={api_key}",
        f"OPENAI_BASE_URL={endpoint}",
        f"OPENAI_MODEL={model}",
        f"OPENCLAW_OPENAI_ENDPOINT={endpoint}",
        f"OPENCLAW_OPENAI_MODEL={model}",
    ]
    gateway_token = (artifacts.get("gateway_token") or "").strip()
    if gateway_token:
        env.append(f"OPENCLAW_GATEWAY_TOKEN={gateway_token}")
        env.append(f"OPENCLAW_GATEWAY_AUTH_TOKEN={gateway_token}")
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
    if cmd_value:
        spec["Cmd"] = shlex.split(cmd_value)
    return spec


def ensure_container_exists(docker, identity, container):
    normalized_identity = normalize_identity(identity)
    users_root = os.getenv("OPENCLAW_USERS_ROOT", "/srv/openclaw/users")
    gateway_token = _ensure_user_gateway_token(
        identity=normalized_identity,
        users_root=users_root,
    )
    ensure_user_runtime(
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
        default_model=os.getenv("OPENCLAW_DEFAULT_OPENAI_MODEL", "gpt-5.2"),
        gateway_token=gateway_token,
    )
    spec = _build_container_spec(container=container, identity=identity, artifacts=artifacts)
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


def start_container_if_needed(docker, container, health_timeout_seconds=15):
    running_ready_seconds = int(os.getenv("OPENCLAW_RUNNING_READY_SECONDS", "20"))
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
            return "started"
        elapsed = health_timeout_seconds - (deadline - time.time())
        if post_running and post_health_status in {"starting", None} and elapsed >= running_ready_seconds:
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

    def _should_use_bootstrap_wait_page(self, parsed_path):
        if parsed_path in {"/health", "/resolve", "/__openclaw__/bootstrap-status"}:
            return False
        if is_websocket_upgrade(self.headers):
            return False
        return is_browser_navigation_request(self.command, self.headers)

    def _handle_bootstrap_status(self, parsed):
        query = parse_qs(parsed.query)
        next_path = normalize_next_path(query.get("next", ["/"])[0])
        message = "OpenClaw instance is waking up..."
        try:
            container = self._resolve_target_container()
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

    def _resolve_target_container(self, query=None):
        query = query or {}
        request_id = self.headers.get("X-Request-Id") or self.headers.get("X-Amzn-Trace-Id")
        header_identity, header_sub = extract_identity(self.headers)
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
            startup_state = start_container_if_needed(DOCKER, container, health_timeout_seconds=timeout)
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
        if provision_state == "created" or startup_state == "started":
            _warm_local_pairing(DOCKER, container)
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

        try:
            container = self._resolve_target_container()
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
