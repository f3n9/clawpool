import unittest
from unittest.mock import patch

from services_instance_manager.main import StartupThrottle, _warm_local_pairing, start_container_if_needed


class FakeDocker:
    def __init__(self, states):
        self.states = states
        self.started = []

    def inspect(self, name, wait=False):
        return {"running": self.states.get(name, False), "healthy": self.states.get(name, False)}

    def start(self, name):
        self.started.append(name)
        self.states[name] = True


class FakeDockerNoHealth:
    def __init__(self, states):
        self.states = states
        self.started = []

    def inspect(self, name, wait=False):
        return {"State": {"Running": self.states.get(name, False)}}

    def start(self, name):
        self.started.append(name)
        self.states[name] = True


class FakeDockerStuckStarting:
    def __init__(self, states):
        self.states = states
        self.started = []

    def inspect(self, name, wait=False):
        return {"running": self.states.get(name, False), "healthy": False}

    def start(self, name):
        self.started.append(name)
        self.states[name] = True


class FakeDockerExec:
    def __init__(self):
        self.calls = []

    def exec_run(self, name, cmd, user="node", timeout_seconds=20):
        self.calls.append(
            {
                "name": name,
                "cmd": cmd,
                "user": user,
                "timeout_seconds": timeout_seconds,
            }
        )
        return 0


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

    def test_running_without_healthcheck_is_treated_as_ready(self):
        docker = FakeDockerNoHealth({"openclaw-u1": True})
        start_container_if_needed(docker, "openclaw-u1")
        self.assertEqual(docker.started, [])

    def test_can_skip_waiting_for_health_when_requested(self):
        docker = FakeDockerStuckStarting({"openclaw-u1": False})
        state = start_container_if_needed(docker, "openclaw-u1", wait_for_ready=False)
        self.assertEqual(state, "started")
        self.assertEqual(docker.started, ["openclaw-u1"])


    def test_start_triggers_local_pairing_warmup(self):
        docker = FakeDocker({"openclaw-u1": False})
        with patch("services_instance_manager.main._warm_local_pairing") as warm_pairing:
            state = start_container_if_needed(docker, "openclaw-u1")
        self.assertEqual(state, "started")
        self.assertEqual(docker.started, ["openclaw-u1"])
        warm_pairing.assert_called_once_with(docker, "openclaw-u1")


    def test_nonblocking_start_triggers_async_local_pairing_warmup(self):
        docker = FakeDockerStuckStarting({"openclaw-u1": False})
        with patch("services_instance_manager.main._warm_local_pairing_async") as warm_pairing_async:
            state = start_container_if_needed(docker, "openclaw-u1", wait_for_ready=False)
        self.assertEqual(state, "started")
        self.assertEqual(docker.started, ["openclaw-u1"])
        warm_pairing_async.assert_called_once_with(docker, "openclaw-u1")


    def test_local_pairing_warmup_retries_until_cli_is_ready(self):
        docker = FakeDockerExec()
        _warm_local_pairing(docker, "openclaw-u1")
        self.assertEqual(len(docker.calls), 1)
        call = docker.calls[0]
        self.assertEqual(call["name"], "openclaw-u1")
        self.assertEqual(call["user"], "node")
        self.assertEqual(call["cmd"][:2], ["sh", "-lc"])
        self.assertIn("for attempt in", call["cmd"][2])
        self.assertIn("openclaw status >/dev/null 2>&1 || true", call["cmd"][2])
        self.assertIn("if openclaw devices approve --latest >/dev/null 2>&1; then exit 0; fi", call["cmd"][2])
        self.assertIn("sleep 1", call["cmd"][2])


if __name__ == "__main__":
    unittest.main()
