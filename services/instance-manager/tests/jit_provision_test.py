import os
import tempfile
import unittest
from unittest.mock import patch

from services_instance_manager.main import (
    DockerAPIError,
    ensure_container_exists,
    extract_groups,
    is_identity_allowed,
    normalize_identity,
    split_csv_values,
)


class FakeDocker:
    def __init__(self):
        self.created = []
        self.existing = set()

    def inspect(self, name, wait=False):
        if name not in self.existing:
            raise DockerAPIError(404, "Not Found", f"/containers/{name}/json")
        return {"State": {"Running": False, "Health": {"Status": "starting"}}}

    def create(self, name, body):
        self.created.append((name, body))
        self.existing.add(name)
        return {"Id": name}


class JITProvisionTests(unittest.TestCase):
    def test_split_csv_values(self):
        self.assertEqual(split_csv_values("a,b, c"), ["a", "b", "c"])

    def test_normalize_identity_for_email(self):
        self.assertEqual(normalize_identity("fyue@yinxiang.com"), "fyue-yinxiang.com")

    def test_extract_groups_accepts_multiple_separators(self):
        headers = {"X-Forwarded-Groups": "ops,dev;ai admin"}
        self.assertEqual(extract_groups(headers), ["ops", "dev", "ai", "admin"])

    def test_identity_allowed_without_constraints(self):
        self.assertTrue(is_identity_allowed({}, [], []))

    def test_identity_allowed_with_domain(self):
        headers = {"X-Forwarded-Email": "alice@example.com"}
        self.assertTrue(is_identity_allowed(headers, ["example.com"], []))
        self.assertFalse(is_identity_allowed(headers, ["corp.internal"], []))

    def test_identity_allowed_with_groups(self):
        headers = {"X-Forwarded-Groups": "team-a,team-b"}
        self.assertTrue(is_identity_allowed(headers, [], ["team-b"]))
        self.assertFalse(is_identity_allowed(headers, [], ["team-c"]))

    def test_creates_user_artifacts_and_container(self):
        docker = FakeDocker()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "k-test",
                "OPENCLAW_DEFAULT_OPENAI_ENDPOINT": "https://api.openai.com/v1",
                "OPENCLAW_DEFAULT_OPENAI_MODEL": "gpt-5.2",
                "OPENCLAW_IMAGE": "ghcr.io/example/openclaw",
                "OPENCLAW_IMAGE_TAG": "1.0.0",
            },
            clear=False,
        ):
            status = ensure_container_exists(docker, identity="u1001", container="openclaw-u1001")
            self.assertEqual(status, "created")
            self.assertEqual(len(docker.created), 1)
            self.assertTrue(os.path.isfile(f"{tmpdir}/u1001/secrets/openai_api_key"))
            self.assertTrue(os.path.isfile(f"{tmpdir}/u1001/secrets/openai_endpoint"))
            self.assertTrue(os.path.isfile(f"{tmpdir}/u1001/secrets/openai_model"))
            self.assertTrue(os.path.isdir(f"{tmpdir}/u1001/runtime"))
            _, spec = docker.created[0]
            binds = spec.get("HostConfig", {}).get("Binds", [])
            self.assertTrue(any(b.endswith(":/home/node/.openclaw") for b in binds))

    def test_fails_when_default_key_missing_for_jit(self):
        docker = FakeDocker()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "",
                "OPENCLAW_IMAGE": "ghcr.io/example/openclaw",
            },
            clear=False,
        ):
            with self.assertRaises(RuntimeError):
                ensure_container_exists(docker, identity="u1002", container="openclaw-u1002")


if __name__ == "__main__":
    unittest.main()
