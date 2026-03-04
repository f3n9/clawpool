import unittest

from resource_controller.main import apply_resource_policy


class FakeDocker:
    def __init__(self, containers):
        self.containers = containers
        self.updated = []

    def list_containers(self):
        return self.containers

    def update_container_resources(self, container_id, nano_cpus, memory_bytes):
        self.updated.append((container_id, nano_cpus, memory_bytes))


class DockerResourceActionsTests(unittest.TestCase):
    def test_boost_profile_applied_when_low_concurrency(self):
        docker = FakeDocker(
            [
                {"Id": "a", "State": "running", "Labels": {"openclaw.managed": "true"}},
                {"Id": "b", "State": "running", "Labels": {"openclaw.managed": "true"}},
            ]
        )
        profile = apply_resource_policy(
            docker,
            boost_threshold=10,
            base_cpu="0.8",
            base_mem="1.2g",
            boost_cpu="1.5",
            boost_mem="2g",
        )
        self.assertEqual(profile, "boost")
        self.assertEqual(len(docker.updated), 2)

    def test_base_profile_applied_when_high_concurrency(self):
        docker = FakeDocker(
            [
                {"Id": str(i), "State": "running", "Labels": {"openclaw.managed": "true"}}
                for i in range(12)
            ]
        )
        profile = apply_resource_policy(
            docker,
            boost_threshold=10,
            base_cpu="0.8",
            base_mem="1.2g",
            boost_cpu="1.5",
            boost_mem="2g",
        )
        self.assertEqual(profile, "base")
        self.assertEqual(len(docker.updated), 12)


if __name__ == "__main__":
    unittest.main()
