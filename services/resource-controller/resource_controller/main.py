#!/usr/bin/env python3
import http.client
import json
import os
import socket


def parse_mem_to_bytes(value):
    text = value.strip().lower()
    if text.endswith("g"):
        return int(float(text[:-1]) * 1024 * 1024 * 1024)
    if text.endswith("m"):
        return int(float(text[:-1]) * 1024 * 1024)
    return int(text)


def cpu_to_nano(cpu_value):
    return int(float(cpu_value) * 1_000_000_000)


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

    def update_container_resources(self, container_id, nano_cpus, memory_bytes):
        self._request(
            "POST",
            f"/containers/{container_id}/update",
            {"NanoCpus": nano_cpus, "Memory": memory_bytes},
        )


def choose_resource_profile(active_instances, boost_threshold):
    if int(active_instances) < int(boost_threshold):
        return "boost"
    return "base"


def collect_managed_running_containers(docker):
    containers = docker.list_containers()
    out = []
    for c in containers:
        if c.get("State") != "running":
            continue
        if c.get("Labels", {}).get("openclaw.managed") != "true":
            continue
        out.append(c)
    return out


def apply_resource_policy(docker, boost_threshold, base_cpu, base_mem, boost_cpu, boost_mem):
    containers = collect_managed_running_containers(docker)
    profile = choose_resource_profile(len(containers), boost_threshold)

    cpu = boost_cpu if profile == "boost" else base_cpu
    mem = boost_mem if profile == "boost" else base_mem

    nano = cpu_to_nano(cpu)
    mem_bytes = parse_mem_to_bytes(mem)

    for c in containers:
        docker.update_container_resources(c["Id"], nano, mem_bytes)

    return profile


def main():
    threshold = int(os.getenv("OPENCLAW_BOOST_THRESHOLD", "10"))
    base_cpu = os.getenv("OPENCLAW_BASE_CPU", "0.8")
    base_mem = os.getenv("OPENCLAW_BASE_MEM", "1.2g")
    boost_cpu = os.getenv("OPENCLAW_BOOST_CPU", "1.5")
    boost_mem = os.getenv("OPENCLAW_BOOST_MEM", "2g")

    if not os.path.exists("/var/run/docker.sock"):
        print("resource-controller: docker socket missing, skip")
        return

    docker = DockerClient()
    profile = apply_resource_policy(
        docker,
        boost_threshold=threshold,
        base_cpu=base_cpu,
        base_mem=base_mem,
        boost_cpu=boost_cpu,
        boost_mem=boost_mem,
    )
    print(f"resource-controller: applied_profile={profile}")


if __name__ == "__main__":
    main()
