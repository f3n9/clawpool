#!/usr/bin/env python3
import http.client
import json
import os
import socket
import time
from datetime import datetime, timezone


class UnixSocketHTTPConnection(http.client.HTTPConnection):
    def __init__(self, unix_socket_path):
        super().__init__("localhost")
        self.unix_socket_path = unix_socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.unix_socket_path)


class DockerClient:
    def __init__(self, socket_path="/var/run/docker.sock"):
        self.socket_path = socket_path

    def _request(self, method, path, body=None):
        conn = UnixSocketHTTPConnection(self.socket_path)
        headers = {}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        data = resp.read()
        if resp.status >= 400:
            raise RuntimeError(f"docker api error {resp.status}: {path}")
        if not data:
            return None
        return json.loads(data.decode("utf-8"))

    def list_containers(self):
        return self._request("GET", "/containers/json") or []

    def inspect_container(self, container_id):
        return self._request("GET", f"/containers/{container_id}/json") or {}

    def stop_container(self, container_id):
        self._request("POST", f"/containers/{container_id}/stop")


def should_stop(last_active_ts, idle_minutes, now_ts=None):
    now = now_ts or int(time.time())
    return (now - int(last_active_ts)) > int(idle_minutes) * 60


def collect_managed_containers(docker):
    containers = docker.list_containers()
    result = []
    for c in containers:
        labels = c.get("Labels", {})
        if c.get("State") != "running":
            continue
        if labels.get("openclaw.managed") != "true":
            continue
        result.append(c)
    return result


def _parse_iso8601_to_ts(value):
    if not value:
        return 0
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return 0


def _read_last_active_marker(users_root, identity):
    if not users_root or not identity:
        return 0
    marker_path = os.path.join(users_root, identity, "runtime", "last_active_ts")
    try:
        with open(marker_path, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def resolve_last_active_ts(docker, container, users_root):
    labels = container.get("Labels", {})
    values = []
    try:
        values.append(int(labels.get("openclaw.last_active_ts", "0")))
    except ValueError:
        values.append(0)
    values.append(_read_last_active_marker(users_root, labels.get("openclaw.identity", "")))
    try:
        details = docker.inspect_container(container["Id"])
    except Exception:
        details = {}
    started_at = details.get("State", {}).get("StartedAt", "")
    values.append(_parse_iso8601_to_ts(started_at))
    return max(values)


def stop_idle_containers(docker, idle_minutes, users_root, now_ts=None):
    stopped = []
    for c in collect_managed_containers(docker):
        labels = c.get("Labels", {})
        last_active = resolve_last_active_ts(docker, c, users_root)
        active_sessions = int(labels.get("openclaw.active_sessions", "0"))
        if active_sessions > 0:
            continue
        if should_stop(last_active, idle_minutes, now_ts=now_ts):
            docker.stop_container(c["Id"])
            stopped.append(c["Id"])
    return stopped


def main():
    idle_minutes = int(os.getenv("OPENCLAW_IDLE_MINUTES", "30"))
    users_root = os.getenv("OPENCLAW_USERS_ROOT", "/srv/openclaw/users")
    if not os.path.exists("/var/run/docker.sock"):
        print("idle-controller: docker socket missing, skip")
        return

    docker = DockerClient()
    stopped = stop_idle_containers(docker, idle_minutes=idle_minutes, users_root=users_root)
    print(f"idle-controller: stopped={len(stopped)}")


if __name__ == "__main__":
    main()
