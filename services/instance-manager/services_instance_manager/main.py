#!/usr/bin/env python3
import json
import http.client
import os
import re
import socket
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
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
            raise RuntimeError(f"docker api error: {resp.status} {resp.reason} path={path}")
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


class DockerClient:
    """In-memory fallback for tests and environments without Docker socket."""

    def __init__(self):
        self._state = {}

    def start(self, name):
        self._state[name] = True

    def inspect(self, name, wait=False):
        running = self._state.get(name, False)
        return {"State": {"Running": running, "Health": {"Status": "healthy" if running else "starting"}}}


def resolve_container_name(employee_id, user_sub, mapping):
    identity = employee_id or user_sub
    if not identity:
        raise ValueError("missing identity")
    if identity in mapping:
        return mapping[identity]
    return f"openclaw-{identity}"


def extract_identity(headers):
    employee_id = (
        headers.get("X-Employee-Id")
        or headers.get("X-Auth-Request-User")
        or headers.get("X-Forwarded-User")
    )
    user_sub = headers.get("X-User-Sub")
    return employee_id, user_sub


def start_container_if_needed(docker, container, health_timeout_seconds=15):
    state = docker.inspect(container)
    running = state.get("running")
    healthy = state.get("healthy")
    if running is None:
        running = state.get("State", {}).get("Running", False)
    if healthy is None:
        healthy = state.get("State", {}).get("Health", {}).get("Status") == "healthy"

    if running and healthy:
        return "running"

    docker.start(container)

    deadline = time.time() + health_timeout_seconds
    while time.time() < deadline:
        post = docker.inspect(container, wait=True)
        post_healthy = post.get("healthy")
        if post_healthy is None:
            post_healthy = post.get("State", {}).get("Health", {}).get("Status") == "healthy"
        if post_healthy:
            return "started"
        time.sleep(0.25)

    raise TimeoutError(f"container {container} failed health check")


MAPPING = {}
if os.path.exists("/var/run/docker.sock"):
    DOCKER = DockerAPIClient()
else:
    DOCKER = DockerClient()
THROTTLE = StartupThrottle(max_concurrent=os.getenv("OPENCLAW_STARTUP_MAX_CONCURRENT", "4"))


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def _resolve_target_container(self, query=None):
        query = query or {}
        header_identity, header_sub = extract_identity(self.headers)
        employee_id = query.get("employee_id", [None])[0] or header_identity
        user_sub = query.get("user_sub", [None])[0] or header_sub
        container = resolve_container_name(employee_id, user_sub, MAPPING)

        if not THROTTLE.try_acquire():
            raise RuntimeError("startup throttled")

        try:
            start_container_if_needed(DOCKER, container)
        finally:
            THROTTLE.release()

        return container

    def _proxy_request(self, container):
        upstream_port = int(os.getenv("OPENCLAW_INSTANCE_PORT", "3000"))
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
            except RuntimeError:
                self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "startup throttled"})
                return
            self._json(HTTPStatus.OK, {"container": container, "state": "ready"})
            return

        try:
            container = self._resolve_target_container()
        except ValueError:
            self._json(HTTPStatus.UNAUTHORIZED, {"error": "missing identity"})
            return
        except RuntimeError:
            self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "startup throttled"})
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
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()
