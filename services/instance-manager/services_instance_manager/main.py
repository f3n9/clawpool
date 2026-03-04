#!/usr/bin/env python3
import json
import os
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


class DockerClient:
    """Minimal wrapper for future Docker API integration."""

    def __init__(self):
        self._state = {}

    def inspect(self, name):
        running = self._state.get(name, False)
        return {"running": running, "healthy": running}

    def start(self, name):
        self._state[name] = True


def resolve_container_name(employee_id, user_sub, mapping):
    identity = employee_id or user_sub
    if not identity:
        raise ValueError("missing identity")
    if identity in mapping:
        return mapping[identity]
    return f"openclaw-{identity}"


def start_container_if_needed(docker, container, health_timeout_seconds=15):
    state = docker.inspect(container)
    if state.get("running"):
        return "running"

    docker.start(container)

    deadline = time.time() + health_timeout_seconds
    while time.time() < deadline:
        post = docker.inspect(container)
        if post.get("healthy"):
            return "started"
        time.sleep(0.25)

    raise TimeoutError(f"container {container} failed health check")


MAPPING = {}
DOCKER = DockerClient()
THROTTLE = StartupThrottle(max_concurrent=os.getenv("OPENCLAW_STARTUP_MAX_CONCURRENT", "4"))


class Handler(BaseHTTPRequestHandler):
    def _json(self, status, payload):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return

        if parsed.path == "/resolve":
            query = parse_qs(parsed.query)
            employee_id = query.get("employee_id", [None])[0] or self.headers.get("X-Employee-Id")
            user_sub = query.get("user_sub", [None])[0] or self.headers.get("X-User-Sub")

            try:
                container = resolve_container_name(employee_id, user_sub, MAPPING)
            except ValueError:
                self._json(HTTPStatus.UNAUTHORIZED, {"error": "missing identity"})
                return

            if not THROTTLE.try_acquire():
                self._json(HTTPStatus.TOO_MANY_REQUESTS, {"error": "startup throttled"})
                return

            try:
                state = start_container_if_needed(DOCKER, container)
            finally:
                THROTTLE.release()

            self._json(HTTPStatus.OK, {"container": container, "state": state})
            return

        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()
