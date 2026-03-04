import unittest

from idle_controller.main import collect_managed_containers, stop_idle_containers


class FakeDocker:
    def __init__(self, containers):
        self.containers = containers
        self.stopped = []

    def list_containers(self):
        return self.containers

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

        stopped = stop_idle_containers(docker, idle_minutes=30, now_ts=2000)
        self.assertEqual(stopped, ["idle-1"])
        self.assertEqual(docker.stopped, ["idle-1"])


if __name__ == "__main__":
    unittest.main()
