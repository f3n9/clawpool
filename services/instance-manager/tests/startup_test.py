import unittest

from services_instance_manager.main import StartupThrottle, start_container_if_needed


class FakeDocker:
    def __init__(self, states):
        self.states = states
        self.started = []

    def inspect(self, name, wait=False):
        return {"running": self.states.get(name, False), "healthy": self.states.get(name, False)}

    def start(self, name):
        self.started.append(name)
        self.states[name] = True


class StartupTests(unittest.TestCase):
    def test_starts_container_when_stopped(self):
        docker = FakeDocker({"openclaw-u1": False})
        start_container_if_needed(docker, "openclaw-u1")
        self.assertEqual(docker.started, ["openclaw-u1"])

    def test_skips_start_when_running(self):
        docker = FakeDocker({"openclaw-u1": True})
        start_container_if_needed(docker, "openclaw-u1")
        self.assertEqual(docker.started, [])

    def test_throttle_limits_parallel_starts(self):
        throttle = StartupThrottle(max_concurrent=1)
        self.assertTrue(throttle.try_acquire())
        self.assertFalse(throttle.try_acquire())
        throttle.release()
        self.assertTrue(throttle.try_acquire())


if __name__ == "__main__":
    unittest.main()
