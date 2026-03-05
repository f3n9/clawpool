import os
import tempfile
import unittest
from unittest.mock import patch
import json

from services_instance_manager.main import (
    DockerAPIError,
    _inject_trusted_proxy_user_header_if_needed,
    is_browser_navigation_request,
    is_retryable_upstream_error,
    normalize_next_path,
    _write_last_active_marker,
    ensure_container_exists,
    extract_identity,
    extract_groups,
    is_websocket_upgrade,
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

    def test_is_browser_navigation_request(self):
        self.assertTrue(is_browser_navigation_request("GET", {"Accept": "text/html,application/xhtml+xml"}))
        self.assertFalse(is_browser_navigation_request("POST", {"Accept": "text/html"}))
        self.assertFalse(is_browser_navigation_request("GET", {"Accept": "application/json"}))

    def test_is_retryable_upstream_error(self):
        self.assertTrue(is_retryable_upstream_error(ConnectionRefusedError(111, "Connection refused")))
        self.assertTrue(is_retryable_upstream_error(RuntimeError("[Errno 111] Connection refused")))
        self.assertFalse(is_retryable_upstream_error(RuntimeError("forbidden")))

    def test_normalize_next_path(self):
        self.assertEqual(normalize_next_path(""), "/")
        self.assertEqual(normalize_next_path("channels?x=1"), "/")
        self.assertEqual(normalize_next_path("/channels?x=1"), "/channels?x=1")
        self.assertEqual(normalize_next_path("/__openclaw__/bootstrap-status"), "/")

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

    def test_extract_identity_prefers_email_headers_before_user(self):
        headers = {
            "X-Auth-Request-User": "32f0a35b-a6a8-4c34-936c-c48d9f11889e",
            "X-Auth-Request-Email": "fyue@yinxiang.com",
            "X-User-Sub": "32f0a35b-a6a8-4c34-936c-c48d9f11889e",
        }
        employee_id, user_sub = extract_identity(headers)
        self.assertEqual(employee_id, "fyue@yinxiang.com")
        self.assertEqual(user_sub, "32f0a35b-a6a8-4c34-936c-c48d9f11889e")

    def test_detect_websocket_upgrade_headers(self):
        self.assertTrue(
            is_websocket_upgrade(
                {
                    "Connection": "keep-alive, Upgrade",
                    "Upgrade": "websocket",
                }
            )
        )
        self.assertFalse(is_websocket_upgrade({"Connection": "keep-alive", "Upgrade": "websocket"}))
        self.assertTrue(
            is_websocket_upgrade(
                {
                    "Upgrade": "websocket",
                    "Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==",
                }
            )
        )

    def test_injects_trusted_proxy_user_header_when_missing(self):
        headers = {
            "X-Forwarded-Email": "fyue@yinxiang.com",
        }
        with patch.dict(
            os.environ,
            {
                "OPENCLAW_GATEWAY_TRUSTED_PROXY_USER_HEADER": "x-forwarded-user",
            },
            clear=False,
        ):
            _inject_trusted_proxy_user_header_if_needed(headers)
        self.assertEqual(headers.get("x-forwarded-user"), "fyue@yinxiang.com")

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
                "OPENCLAW_HOST": "claw.hatch.yinxiang.com",
                "OPENCLAW_INSTANCE_PORT": "18789",
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
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertEqual(cfg.get("gateway", {}).get("bind"), "lan")
            self.assertEqual(cfg.get("gateway", {}).get("port"), 18789)
            self.assertEqual(
                cfg.get("gateway", {}).get("controlUi", {}).get("allowedOrigins"),
                ["https://claw.hatch.yinxiang.com"],
            )
            self.assertEqual(cfg.get("gateway", {}).get("auth", {}).get("mode"), "trusted-proxy")
            self.assertTrue(cfg.get("gateway", {}).get("auth", {}).get("token"))
            self.assertEqual(
                cfg.get("gateway", {}).get("auth", {}).get("trustedProxy", {}).get("userHeader"),
                "host",
            )
            self.assertEqual(
                cfg.get("gateway", {}).get("trustedProxies"),
                ["127.0.0.1/32", "172.16.0.0/12"],
            )
            self.assertTrue(
                cfg.get("plugins", {}).get("entries", {}).get("telegram", {}).get("enabled"),
            )
            self.assertTrue(
                cfg.get("plugins", {}).get("entries", {}).get("googlechat", {}).get("enabled"),
            )
            self.assertEqual(
                cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary"),
                "openai/gpt-5.2",
            )
            self.assertIn(
                "openai/gpt-5.2",
                cfg.get("agents", {}).get("defaults", {}).get("models", {}),
            )
            self.assertEqual(
                cfg.get("agents", {}).get("defaults", {}).get("models", {}).get("openai/gpt-5.2", {}).get(
                    "params", {}
                ).get("transport"),
                "sse",
            )
            self.assertEqual(
                cfg.get("models", {}).get("providers", {}).get("openai", {}).get("baseUrl"),
                "https://api.openai.com/v1",
            )
            self.assertEqual(
                cfg.get("models", {}).get("providers", {}).get("openai", {}).get("api"),
                "openai-responses",
            )
            self.assertEqual(
                cfg.get("models", {}).get("providers", {}).get("openai", {}).get("models", [{}])[0].get("id"),
                "gpt-5.2",
            )
            _, spec = docker.created[0]
            binds = spec.get("HostConfig", {}).get("Binds", [])
            self.assertTrue(any(b.endswith(":/home/node/.openclaw") for b in binds))
            env_entries = spec.get("Env", [])
            self.assertTrue(any(e.startswith("OPENCLAW_GATEWAY_TOKEN=") for e in env_entries))
            self.assertTrue(any(e.startswith("OPENCLAW_GATEWAY_AUTH_TOKEN=") for e in env_entries))

    def test_repairs_legacy_trusted_proxy_config(self):
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
                "OPENCLAW_GATEWAY_AUTH_MODE": "trusted-proxy",
                "OPENCLAW_GATEWAY_TRUSTED_PROXY_USER_HEADER": "x-forwarded-email",
                "OPENCLAW_GATEWAY_TRUSTED_PROXIES": "127.0.0.1/32,172.16.0.0/12",
            },
            clear=False,
        ):
            os.makedirs(f"{tmpdir}/u1001/runtime", exist_ok=True)
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "gateway": {
                            "auth": {
                                "mode": "trusted-proxy",
                                "trustedProxy": {
                                    "emailHeader": "x-forwarded-email",
                                    "cidrs": ["172.16.0.0/12"],
                                },
                            }
                        }
                    },
                    f,
                )
            status = ensure_container_exists(docker, identity="u1001", container="openclaw-u1001")
            self.assertEqual(status, "created")
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            trusted_proxy = cfg.get("gateway", {}).get("auth", {}).get("trustedProxy", {})
            self.assertEqual(trusted_proxy.get("userHeader"), "x-forwarded-email")
            self.assertNotIn("emailHeader", trusted_proxy)
            self.assertNotIn("cidrs", trusted_proxy)
            self.assertEqual(
                cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary"),
                "openai/gpt-5.2",
            )
            self.assertEqual(
                cfg.get("agents", {}).get("defaults", {}).get("models", {}).get("openai/gpt-5.2", {}).get(
                    "params", {}
                ).get("transport"),
                "sse",
            )

    def test_repairs_existing_container_runtime_without_requiring_default_key(self):
        docker = FakeDocker()
        docker.existing.add("openclaw-u1001")
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "",
                "OPENCLAW_GATEWAY_AUTH_MODE": "trusted-proxy",
                "OPENCLAW_GATEWAY_TRUSTED_PROXIES": "127.0.0.1/32,172.16.0.0/12",
            },
            clear=False,
        ):
            os.makedirs(f"{tmpdir}/u1001/runtime", exist_ok=True)
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "gateway": {
                            "auth": {
                                "mode": "trusted-proxy",
                                "trustedProxy": {
                                    "emailHeader": "x-forwarded-email",
                                },
                            }
                        }
                    },
                    f,
                )
            status = ensure_container_exists(docker, identity="u1001", container="openclaw-u1001")
            self.assertEqual(status, "existing")
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            trusted_proxy = cfg.get("gateway", {}).get("auth", {}).get("trustedProxy", {})
            self.assertTrue(cfg.get("gateway", {}).get("auth", {}).get("token"))
            self.assertEqual(trusted_proxy.get("userHeader"), "host")
            self.assertNotIn("emailHeader", trusted_proxy)
            self.assertEqual(
                cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary"),
                "openai/gpt-5.2",
            )
            self.assertEqual(
                cfg.get("agents", {}).get("defaults", {}).get("models", {}).get("openai/gpt-5.2", {}).get(
                    "params", {}
                ).get("transport"),
                "sse",
            )

    def test_repairs_gateway_auth_mode_from_token_to_trusted_proxy(self):
        docker = FakeDocker()
        docker.existing.add("openclaw-u1001")
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "",
                "OPENCLAW_GATEWAY_AUTH_MODE": "trusted-proxy",
                "OPENCLAW_GATEWAY_TRUSTED_PROXY_USER_HEADER": "x-forwarded-user",
                "OPENCLAW_GATEWAY_TRUSTED_PROXIES": "127.0.0.1/32,172.16.0.0/12",
            },
            clear=False,
        ):
            os.makedirs(f"{tmpdir}/u1001/runtime", exist_ok=True)
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "gateway": {
                            "auth": {
                                "mode": "token",
                                "token": "legacy-token",
                                "trustedProxy": {
                                    "userHeader": "x-forwarded-user",
                                },
                            }
                        }
                    },
                    f,
                )
            status = ensure_container_exists(docker, identity="u1001", container="openclaw-u1001")
            self.assertEqual(status, "existing")
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertEqual(cfg.get("gateway", {}).get("auth", {}).get("mode"), "trusted-proxy")
            self.assertEqual(
                cfg.get("gateway", {}).get("auth", {}).get("trustedProxy", {}).get("userHeader"),
                "x-forwarded-user",
            )

    def test_repairs_anthropic_primary_model_to_openai_default(self):
        docker = FakeDocker()
        docker.existing.add("openclaw-u1001")
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_MODEL": "gpt-5.3-codex",
                "OPENCLAW_ALLOWED_MODELS": "gpt-5.2,gpt-5.3-codex",
                "OPENCLAW_DEFAULT_OPENAI_KEY": "",
            },
            clear=False,
        ):
            os.makedirs(f"{tmpdir}/u1001/runtime", exist_ok=True)
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "agents": {
                            "defaults": {
                                "model": {"primary": "anthropic/claude-opus-4-6"},
                                "models": {"anthropic/claude-opus-4-6": {}},
                            }
                        }
                    },
                    f,
                )
            status = ensure_container_exists(docker, identity="u1001", container="openclaw-u1001")
            self.assertEqual(status, "existing")
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertEqual(
                cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary"),
                "openai/gpt-5.3-codex",
            )
            self.assertIn(
                "openai/gpt-5.3-codex",
                cfg.get("agents", {}).get("defaults", {}).get("models", {}),
            )
            self.assertEqual(
                cfg.get("agents", {}).get("defaults", {}).get("models", {}).get("openai/gpt-5.3-codex", {}).get(
                    "params", {}
                ).get("transport"),
                "sse",
            )

    def test_channel_plugins_default_enabled_without_overriding_explicit_false(self):
        docker = FakeDocker()
        docker.existing.add("openclaw-u1001")
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "",
                "OPENCLAW_DEFAULT_CHANNEL_PLUGINS": "telegram,googlechat",
            },
            clear=False,
        ):
            os.makedirs(f"{tmpdir}/u1001/runtime", exist_ok=True)
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "plugins": {
                            "entries": {
                                "telegram": {"enabled": False},
                            }
                        }
                    },
                    f,
                )
            status = ensure_container_exists(docker, identity="u1001", container="openclaw-u1001")
            self.assertEqual(status, "existing")
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertFalse(cfg.get("plugins", {}).get("entries", {}).get("telegram", {}).get("enabled"))
            self.assertTrue(cfg.get("plugins", {}).get("entries", {}).get("googlechat", {}).get("enabled"))

    def test_repairs_local_device_pairing_scopes_for_cli(self):
        docker = FakeDocker()
        docker.existing.add("openclaw-u1001")
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "",
            },
            clear=False,
        ):
            os.makedirs(f"{tmpdir}/u1001/runtime/identity", exist_ok=True)
            os.makedirs(f"{tmpdir}/u1001/runtime/devices", exist_ok=True)
            with open(f"{tmpdir}/u1001/runtime/identity/device.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 1,
                        "deviceId": "dev-1",
                        "publicKeyPem": "-----BEGIN PUBLIC KEY-----\\nabc\\n-----END PUBLIC KEY-----\\n",
                    },
                    f,
                )
            with open(f"{tmpdir}/u1001/runtime/devices/paired.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "dev-1": {
                            "deviceId": "dev-1",
                            "publicKey": "abc",
                            "role": "operator",
                            "roles": ["operator"],
                            "scopes": ["operator.read"],
                            "approvedScopes": ["operator.read"],
                            "tokens": {
                                "operator": {
                                    "token": "tok-1",
                                    "role": "operator",
                                    "scopes": ["operator.read"],
                                }
                            },
                        }
                    },
                    f,
                )
            with open(f"{tmpdir}/u1001/runtime/devices/pending.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "req-1": {
                            "requestId": "req-1",
                            "deviceId": "dev-1",
                            "role": "operator",
                            "scopes": ["operator.admin", "operator.read", "operator.write"],
                            "ts": 123,
                        }
                    },
                    f,
                )

            status = ensure_container_exists(docker, identity="u1001", container="openclaw-u1001")
            self.assertEqual(status, "existing")

            with open(f"{tmpdir}/u1001/runtime/devices/paired.json", "r", encoding="utf-8") as f:
                paired = json.load(f)
            with open(f"{tmpdir}/u1001/runtime/devices/pending.json", "r", encoding="utf-8") as f:
                pending = json.load(f)

            scopes = paired.get("dev-1", {}).get("scopes", [])
            for required in [
                "operator.admin",
                "operator.read",
                "operator.write",
                "operator.approvals",
                "operator.pairing",
            ]:
                self.assertIn(required, scopes)
                self.assertIn(required, paired.get("dev-1", {}).get("approvedScopes", []))
                self.assertIn(
                    required,
                    paired.get("dev-1", {}).get("tokens", {}).get("operator", {}).get("scopes", []),
                )
            self.assertEqual(pending, {})

    def test_promotes_pending_pairing_request_when_paired_missing(self):
        docker = FakeDocker()
        docker.existing.add("openclaw-u1001")
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "",
            },
            clear=False,
        ):
            os.makedirs(f"{tmpdir}/u1001/runtime/identity", exist_ok=True)
            os.makedirs(f"{tmpdir}/u1001/runtime/devices", exist_ok=True)
            with open(f"{tmpdir}/u1001/runtime/identity/device.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 1,
                        "deviceId": "dev-2",
                        "publicKeyPem": "-----BEGIN PUBLIC KEY-----\\nabc\\n-----END PUBLIC KEY-----\\n",
                    },
                    f,
                )
            with open(f"{tmpdir}/u1001/runtime/devices/paired.json", "w", encoding="utf-8") as f:
                json.dump({}, f)
            with open(f"{tmpdir}/u1001/runtime/devices/pending.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "req-1": {
                            "requestId": "req-1",
                            "deviceId": "dev-2",
                            "publicKey": "pk-2",
                            "platform": "linux",
                            "clientId": "cli",
                            "clientMode": "probe",
                            "role": "operator",
                            "scopes": ["operator.read"],
                            "ts": 123,
                        }
                    },
                    f,
                )

            status = ensure_container_exists(docker, identity="u1001", container="openclaw-u1001")
            self.assertEqual(status, "existing")

            with open(f"{tmpdir}/u1001/runtime/devices/paired.json", "r", encoding="utf-8") as f:
                paired = json.load(f)
            with open(f"{tmpdir}/u1001/runtime/devices/pending.json", "r", encoding="utf-8") as f:
                pending = json.load(f)

            self.assertIn("dev-2", paired)
            self.assertEqual(paired.get("dev-2", {}).get("role"), "operator")
            self.assertIn("operator.admin", paired.get("dev-2", {}).get("scopes", []))
            self.assertEqual(pending, {})

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

    def test_write_last_active_marker(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_CONTAINER_UID": str(os.getuid()),
                "OPENCLAW_CONTAINER_GID": str(os.getgid()),
            },
            clear=False,
        ):
            _write_last_active_marker("fyue@yinxiang.com", tmpdir)
            marker = f"{tmpdir}/fyue-yinxiang.com/runtime/last_active_ts"
            self.assertTrue(os.path.isfile(marker))
            with open(marker, "r", encoding="utf-8") as f:
                self.assertTrue(f.read().strip().isdigit())


if __name__ == "__main__":
    unittest.main()
