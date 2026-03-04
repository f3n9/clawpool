#!/usr/bin/env python3
import json
import http.client
import os
import re
import select
import shlex
import socket
import threading
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


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
    upgrade = (headers.get("Upgrade") or "").strip().lower()
    connection = (headers.get("Connection") or "").strip().lower()
    return upgrade == "websocket" and "upgrade" in connection


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


def _ensure_runtime_config(runtime_dir, uid, gid):
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
    auth.setdefault("mode", auth_mode)
    token = os.getenv("OPENCLAW_GATEWAY_AUTH_TOKEN", "").strip()
    if token and not auth.get("token"):
        auth["token"] = token

    if auth.get("mode") == "trusted-proxy":
        trusted_proxy = auth.get("trustedProxy")
        if not isinstance(trusted_proxy, dict):
            trusted_proxy = {}
        # Remove keys that were used in prior failed experiments and are rejected by OpenClaw schema.
        trusted_proxy.pop("emailHeader", None)
        trusted_proxy.pop("cidrs", None)
        user_header = trusted_proxy.get("userHeader")
        if not isinstance(user_header, str) or not user_header.strip():
            trusted_proxy["userHeader"] = (
                os.getenv("OPENCLAW_GATEWAY_TRUSTED_PROXY_USER_HEADER", "x-forwarded-user").strip()
                or "x-forwarded-user"
            )
        auth["trustedProxy"] = trusted_proxy

        trusted_proxies = gateway.get("trustedProxies")
        if not isinstance(trusted_proxies, list) or not trusted_proxies:
            gateway["trustedProxies"] = split_csv_values(
                os.getenv("OPENCLAW_GATEWAY_TRUSTED_PROXIES", "127.0.0.1/32,172.16.0.0/12")
            )

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=True, indent=2)
        f.write("\n")
    os.chmod(config_path, 0o600)
    _safe_chown(config_path, uid, gid)


def ensure_user_runtime(identity, users_root):
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
    _ensure_runtime_config(runtime_dir, container_uid, container_gid)
    return {
        "data_dir": data_dir,
        "config_dir": config_dir,
        "runtime_dir": runtime_dir,
    }


def ensure_user_artifacts(identity, users_root, default_key, default_endpoint, default_model):
    runtime = ensure_user_runtime(identity, users_root)
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
    ensure_user_runtime(
        identity=normalize_identity(identity),
        users_root=os.getenv("OPENCLAW_USERS_ROOT", "/srv/openclaw/users"),
    )
    try:
        docker.inspect(container)
        return "existing"
    except DockerAPIError as exc:
        if exc.status != 404:
            raise

    artifacts = ensure_user_artifacts(
        identity=normalize_identity(identity),
        users_root=os.getenv("OPENCLAW_USERS_ROOT", "/srv/openclaw/users"),
        default_key=os.getenv("OPENCLAW_DEFAULT_OPENAI_KEY", "").strip(),
        default_endpoint=os.getenv("OPENCLAW_DEFAULT_OPENAI_ENDPOINT", "https://api.openai.com/v1"),
        default_model=os.getenv("OPENCLAW_DEFAULT_OPENAI_MODEL", "gpt-5.2"),
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

        try:
            request_lines = [f"{self.command} {self.path} HTTP/1.1"]
            has_host = False
            for key, value in self.headers.items():
                if key.lower() == "host":
                    has_host = True
                    request_lines.append(f"Host: {container}:{upstream_port}")
                else:
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
                return

            client.setblocking(False)
            upstream.setblocking(False)
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
                    except OSError:
                        return
                    if not data:
                        return
                    target = upstream if sock is client else client
                    target.sendall(data)
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

        if parsed.path == "/resolve":
            query = parse_qs(parsed.query)

            try:
                container = self._resolve_target_container(query=query)
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
            self._json(HTTPStatus.OK, {"container": container, "state": "ready"})
            return

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

        if not re.match(r"^[a-zA-Z0-9._-]+$", container):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid container name"})
            return

        try:
            self._proxy_request(container)
        except Exception as exc:
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
