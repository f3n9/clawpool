import json
import os
import tempfile
import unittest
from unittest.mock import patch

from services_instance_manager.main import (
    StartupThrottle,
    _wait_for_local_pairing_identity,
    _warm_local_pairing,
    start_container_if_needed,
)


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


    def test_nonblocking_start_prefers_runtime_pairing_repair_when_runtime_known(self):
        docker = FakeDockerStuckStarting({"openclaw-u1": False})
        with patch("services_instance_manager.main._schedule_local_pairing_repair") as schedule_pairing_repair, patch(
            "services_instance_manager.main._warm_local_pairing_async"
        ) as warm_pairing_async:
            state = start_container_if_needed(
                docker,
                "openclaw-u1",
                wait_for_ready=False,
                runtime_dir="/tmp/u1/runtime",
                container_uid=1000,
                container_gid=1000,
            )
        self.assertEqual(state, "started")
        self.assertEqual(docker.started, ["openclaw-u1"])
        schedule_pairing_repair.assert_called_once_with("/tmp/u1/runtime", 1000, 1000, timeout_seconds=15)
        warm_pairing_async.assert_not_called()


    def test_blocking_start_prefers_runtime_pairing_repair_when_runtime_known(self):
        docker = FakeDocker({"openclaw-u1": False})
        with patch("services_instance_manager.main._schedule_local_pairing_repair") as schedule_pairing_repair, patch(
            "services_instance_manager.main._warm_local_pairing"
        ) as warm_pairing:
            state = start_container_if_needed(
                docker,
                "openclaw-u1",
                runtime_dir="/tmp/u1/runtime",
                container_uid=1000,
                container_gid=1000,
            )
        self.assertEqual(state, "started")
        self.assertEqual(docker.started, ["openclaw-u1"])
        schedule_pairing_repair.assert_called_once_with("/tmp/u1/runtime", 1000, 1000, timeout_seconds=15)
        warm_pairing.assert_not_called()


    def test_wait_for_local_pairing_identity_retries_until_device_is_paired(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_LOCAL_PAIRING_REPAIR_TIMEOUT_SECONDS": "1",
                "OPENCLAW_LOCAL_PAIRING_REPAIR_POLL_SECONDS": "0.01",
            },
            clear=False,
        ):
            runtime_dir = f"{tmpdir}/runtime"
            os.makedirs(f"{runtime_dir}/identity", exist_ok=True)
            os.makedirs(f"{runtime_dir}/devices", exist_ok=True)
            with open(f"{runtime_dir}/identity/device.json", "w", encoding="utf-8") as f:
                json.dump({"deviceId": "dev-1"}, f)
            with open(f"{runtime_dir}/devices/paired.json", "w", encoding="utf-8") as f:
                json.dump({}, f)
            with open(f"{runtime_dir}/devices/pending.json", "w", encoding="utf-8") as f:
                json.dump({}, f)

            repair_calls = []

            def fake_repair(current_runtime_dir, uid, gid):
                repair_calls.append((current_runtime_dir, uid, gid))
                if len(repair_calls) == 1:
                    with open(f"{runtime_dir}/devices/pending.json", "w", encoding="utf-8") as f:
                        json.dump({"req-1": {"deviceId": "dev-1"}}, f)
                    return
                with open(f"{runtime_dir}/devices/paired.json", "w", encoding="utf-8") as f:
                    json.dump({"dev-1": {"deviceId": "dev-1"}}, f)
                with open(f"{runtime_dir}/devices/pending.json", "w", encoding="utf-8") as f:
                    json.dump({}, f)

            with patch("services_instance_manager.main._repair_local_device_pairing", side_effect=fake_repair):
                _wait_for_local_pairing_identity(runtime_dir, 1000, 1000, timeout_seconds=1)

            self.assertEqual(len(repair_calls), 2)
            with open(f"{runtime_dir}/devices/paired.json", "r", encoding="utf-8") as f:
                paired = json.load(f)
            self.assertIn("dev-1", paired)

    def test_wait_for_local_pairing_identity_uses_health_timeout_by_default(self):
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_HEALTH_TIMEOUT_SECONDS": "77",
                "OPENCLAW_LOCAL_PAIRING_REPAIR_POLL_SECONDS": "0.01",
            },
            clear=False,
        ), patch("services_instance_manager.main.os.path.exists", return_value=False), patch(
            "services_instance_manager.main.time.sleep"
        ) as sleep_mock, patch("services_instance_manager.main.time.time", side_effect=[0, 1, 78]):
            _wait_for_local_pairing_identity("/tmp/runtime", 1000, 1000)

        self.assertGreaterEqual(sleep_mock.call_count, 1)




    def test_local_pairing_warmup_runs_single_approve_command(self):
        docker = FakeDockerExec()
        _warm_local_pairing(docker, "openclaw-u1")
        self.assertEqual(len(docker.calls), 1)
        call = docker.calls[0]
        self.assertEqual(call["name"], "openclaw-u1")
        self.assertEqual(call["user"], "node")
        self.assertEqual(call["cmd"][:2], ["sh", "-lc"])
        self.assertEqual(call["cmd"][2], "openclaw devices approve --latest >/dev/null 2>&1 || true")


if __name__ == "__main__":
    unittest.main()
