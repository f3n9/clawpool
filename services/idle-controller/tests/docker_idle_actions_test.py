import unittest
import tempfile
import os

from idle_controller.main import collect_managed_containers, stop_idle_containers


class FakeDocker:
    def __init__(self, containers, inspect_map=None):
        self.containers = containers
        self.stopped = []
        self.inspect_map = inspect_map or {}

    def list_containers(self):
        return self.containers

    def inspect_container(self, container_id):
        return self.inspect_map.get(container_id, {})

    def stop_container(self, container_id):
        self.stopped.append(container_id)


class DockerIdleActionsTests(unittest.TestCase):
    def test_collects_only_running_managed_containers(self):
        docker = FakeDocker(
            [
                {"Id": "1", "State": "running", "Labels": {"openclaw.managed": "true"}},
                {"Id": "2", "State": "exited", "Labels": {"openclaw.managed": "true"}},
                {"Id": "3", "State": "running", "Labels": {}},
            ]
        )
        containers = collect_managed_containers(docker)
        self.assertEqual([c["Id"] for c in containers], ["1"])

    def test_stops_only_idle_and_non_active_session(self):
        docker = FakeDocker(
            [
                {
                    "Id": "idle-1",
                    "State": "running",
                    "Labels": {
                        "openclaw.managed": "true",
                        "openclaw.last_active_ts": "100",
                        "openclaw.active_sessions": "0",
                    },
                },
                {
                    "Id": "busy-1",
                    "State": "running",
                    "Labels": {
                        "openclaw.managed": "true",
                        "openclaw.last_active_ts": "1950",
                        "openclaw.active_sessions": "0",
                    },
                },
                {
                    "Id": "active-session",
                    "State": "running",
                    "Labels": {
                        "openclaw.managed": "true",
                        "openclaw.last_active_ts": "100",
                        "openclaw.active_sessions": "2",
                    },
                },
            ]
        )

        stopped = stop_idle_containers(docker, idle_minutes=30, users_root="/tmp/none", now_ts=2000)
        self.assertEqual(stopped, ["idle-1"])
        self.assertEqual(docker.stopped, ["idle-1"])

    def test_uses_last_active_marker_for_idle_decision(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            identity = "fyue-yinxiang.com"
            marker_dir = f"{tmpdir}/{identity}/runtime"
            os.makedirs(marker_dir, exist_ok=True)
            with open(f"{marker_dir}/last_active_ts", "w", encoding="utf-8") as f:
                f.write("1950\n")

            docker = FakeDocker(
                [
                    {
                        "Id": "recent-by-marker",
                        "State": "running",
                        "Labels": {
                            "openclaw.managed": "true",
                            "openclaw.identity": identity,
                            "openclaw.last_active_ts": "100",
                            "openclaw.active_sessions": "0",
                        },
                    }
                ]
            )
            stopped = stop_idle_containers(docker, idle_minutes=30, users_root=tmpdir, now_ts=2000)
            self.assertEqual(stopped, [])


if __name__ == "__main__":
    unittest.main()
