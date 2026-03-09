import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import json

import services_instance_manager.main as instance_manager_main
from services_instance_manager.main import (
    CONSOLE_STATIC_ROOT,
    DockerAPIError,
    Handler,
    _build_default_startup_cmd,
    _parse_console_control,
    _inject_trusted_proxy_user_header_if_needed,
    is_browser_navigation_request,
    is_retryable_upstream_error,
    normalize_next_path,
    _websocket_accept_key,
    _ws_read_frame,
    _ws_send_frame,
    _write_last_active_marker,
    ensure_container_exists,
    extract_identity,
    extract_groups,
    is_websocket_upgrade,
    is_identity_allowed,
    normalize_identity,
    should_allow_loopback_query_identity,
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
    def test_console_static_assets_exist(self):
        self.assertTrue((CONSOLE_STATIC_ROOT / "xterm.js").is_file())
        self.assertTrue((CONSOLE_STATIC_ROOT / "xterm.css").is_file())
        self.assertTrue((CONSOLE_STATIC_ROOT / "xterm-addon-fit.js").is_file())

    def test_split_csv_values(self):
        self.assertEqual(split_csv_values("a,b, c"), ["a", "b", "c"])

    def test_is_browser_navigation_request(self):
        self.assertTrue(is_browser_navigation_request("GET", {"Accept": "text/html,application/xhtml+xml"}))
        self.assertFalse(is_browser_navigation_request("POST", {"Accept": "text/html"}))
        self.assertFalse(is_browser_navigation_request("GET", {"Accept": "application/json"}))

    def test_resolve_uses_bootstrap_wait_page_for_browser_navigation(self):
        handler = Handler.__new__(Handler)
        handler.command = "GET"
        handler.headers = {"Accept": "text/html,application/xhtml+xml"}
        self.assertTrue(handler._should_use_bootstrap_wait_page("/resolve"))
        self.assertFalse(handler._should_use_bootstrap_wait_page("/__openclaw__/bootstrap-status"))

    def test_nonblocking_resolve_does_not_spawn_pairing_warmup_thread(self):
        handler = Handler.__new__(Handler)
        handler.command = "GET"
        handler.headers = {"X-Forwarded-Email": "fyue@yinxiang.com", "Accept": "text/html"}
        handler.client_address = ("127.0.0.1", 12345)

        with patch.dict(os.environ, {"OPENCLAW_JIT_PROVISION": "false"}, clear=False), patch(
            "services_instance_manager.main.is_identity_allowed", return_value=True
        ), patch(
            "services_instance_manager.main.start_container_if_needed", return_value="started"
        ), patch(
            "services_instance_manager.main._write_last_active_marker"
        ), patch(
            "services_instance_manager.main.read_container_runtime_state",
            return_value={"running": True, "health": "starting"},
        ), patch(
            "services_instance_manager.main.emit_identity_audit"
        ), patch(
            "services_instance_manager.main._warm_local_pairing"
        ) as warm_pairing, patch.object(
            instance_manager_main.THROTTLE, "try_acquire", return_value=True
        ), patch.object(
            instance_manager_main.THROTTLE, "release"
        ), patch(
            "services_instance_manager.main.threading.Thread"
        ) as thread_cls:
            container = handler._resolve_target_container(wait_for_ready=False)

        self.assertEqual(container, "openclaw-fyue-yinxiang.com")
        thread_cls.assert_not_called()
        warm_pairing.assert_not_called()

    def test_is_retryable_upstream_error(self):
        self.assertTrue(is_retryable_upstream_error(ConnectionRefusedError(111, "Connection refused")))
        self.assertTrue(is_retryable_upstream_error(RuntimeError("[Errno 111] Connection refused")))
        self.assertFalse(is_retryable_upstream_error(RuntimeError("forbidden")))

    def test_normalize_next_path(self):
        self.assertEqual(normalize_next_path(""), "/")
        self.assertEqual(normalize_next_path("channels?x=1"), "/")
        self.assertEqual(normalize_next_path("/channels?x=1"), "/channels?x=1")
        self.assertEqual(normalize_next_path("/__openclaw__/bootstrap-status"), "/")

    def test_websocket_accept_key(self):
        self.assertEqual(
            _websocket_accept_key("dGhlIHNhbXBsZSBub25jZQ=="),
            "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
        )

    def test_ws_read_and_send_frame(self):
        class DummySocket:
            def __init__(self, incoming=b""):
                self._incoming = bytearray(incoming)
                self.sent = bytearray()

            def recv(self, n):
                if not self._incoming:
                    return b""
                chunk = self._incoming[:n]
                del self._incoming[:n]
                return bytes(chunk)

            def sendall(self, data):
                self.sent.extend(data)

        # client -> server: masked text frame "hello"
        payload = b"hello"
        mask = b"\x01\x02\x03\x04"
        masked = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
        frame = bytes([0x81, 0x80 | len(payload)]) + mask + masked
        in_sock = DummySocket(frame)
        opcode, body = _ws_read_frame(in_sock)
        self.assertEqual(opcode, 1)
        self.assertEqual(body, payload)

        # server -> client: binary frame "world"
        out_sock = DummySocket()
        _ws_send_frame(out_sock, b"world", opcode=2)
        raw = bytes(out_sock.sent)
        self.assertEqual(raw[0], 0x82)
        length = raw[1] & 0x7F
        idx = 2
        if length == 126:
            length = struct.unpack("!H", raw[idx : idx + 2])[0]
            idx += 2
        elif length == 127:
            length = struct.unpack("!Q", raw[idx : idx + 8])[0]
            idx += 8
        self.assertEqual(raw[idx : idx + length], b"world")

    def test_parse_console_control_resize(self):
        ctrl = _parse_console_control(b'{"type":"resize","cols":120,"rows":40}')
        self.assertEqual(ctrl, {"type": "resize", "cols": 120, "rows": 40})
        self.assertIsNone(_parse_console_control(b'{"type":"resize","cols":0,"rows":40}'))
        self.assertIsNone(_parse_console_control(b'{"type":"resize","cols":"x","rows":40}'))
        self.assertIsNone(_parse_console_control(b'{"type":"noop"}'))

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

    def test_loopback_query_identity_override_only_without_auth_headers(self):
        self.assertTrue(
            should_allow_loopback_query_identity(("127.0.0.1", 12345), None, None)
        )
        self.assertTrue(
            should_allow_loopback_query_identity(("::1", 12345), None, None)
        )
        self.assertFalse(
            should_allow_loopback_query_identity(("10.0.0.8", 12345), None, None)
        )
        self.assertFalse(
            should_allow_loopback_query_identity(("127.0.0.1", 12345), "u1001", None)
        )

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
            self.assertNotIn(
                "telegram",
                cfg.get("plugins", {}).get("entries", {}),
            )
            self.assertTrue(
                cfg.get("plugins", {}).get("entries", {}).get("wecom", {}).get("enabled"),
            )
            self.assertTrue(
                cfg.get("tools", {}).get("media", {}).get("image", {}).get("enabled"),
            )
            self.assertEqual(cfg.get("tools", {}).get("profile"), "full")
            self.assertEqual(
                cfg.get("tools", {}).get("sessions", {}).get("visibility"),
                "all",
            )
            self.assertEqual(
                cfg.get("browser", {}).get("executablePath"),
                "/usr/local/bin/openclaw-chromium",
            )
            self.assertTrue(cfg.get("browser", {}).get("headless"))
            self.assertTrue(cfg.get("browser", {}).get("noSandbox"))
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
            cmd = spec.get("Cmd", [])
            self.assertEqual(cmd[:2], ["sh", "-lc"])
            self.assertIn("openai-responses.js", cmd[2] if len(cmd) > 2 else "")
            self.assertIn("openai-responses-shared.js", cmd[2] if len(cmd) > 2 else "")
            self.assertNotIn("/opt/openclaw/extensions", cmd[2] if len(cmd) > 2 else "")
            self.assertIn("/app/extensions", cmd[2] if len(cmd) > 2 else "")
            self.assertIn("channels[channelId]", cmd[2] if len(cmd) > 2 else "")
            self.assertIn("store: true", cmd[2] if len(cmd) > 2 else "")
            self.assertIn("thinkingSignature", cmd[2] if len(cmd) > 2 else "")
            self.assertIn("textSignature", cmd[2] if len(cmd) > 2 else "")
            self.assertIn("msgId = undefined", cmd[2] if len(cmd) > 2 else "")
            self.assertIn("if (false", cmd[2] if len(cmd) > 2 else "")

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
                "openai/gpt-5.3-chat",
            )
            self.assertEqual(
                cfg.get("agents", {}).get("defaults", {}).get("models", {}).get("openai/gpt-5.3-chat", {}).get(
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

    def test_prunes_legacy_openai_models_not_in_allowed_list(self):
        docker = FakeDocker()
        docker.existing.add("openclaw-u1001")
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_MODEL": "gpt-5.3-chat",
                "OPENCLAW_ALLOWED_MODELS": "gpt-5.2,gpt-5.3-codex,gpt-5.3-chat",
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
                                "model": {"primary": "openai/gpt-5.2-chat"},
                                "models": {
                                    "openai/gpt-5.2-chat": {},
                                    "openai/gpt-5.2": {},
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
            self.assertEqual(
                cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary"),
                "openai/gpt-5.3-chat",
            )
            models = cfg.get("agents", {}).get("defaults", {}).get("models", {})
            self.assertNotIn("openai/gpt-5.2-chat", models)
            self.assertIn("openai/gpt-5.2", models)
            self.assertIn("openai/gpt-5.3-chat", models)

    def test_defaults_to_openai_responses_even_with_chat_or_kimi_models(self):
        docker = FakeDocker()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "k-test",
                "OPENCLAW_DEFAULT_OPENAI_ENDPOINT": "https://api.openai.com/v1",
                "OPENCLAW_ALLOWED_MODELS": "gpt-5.2,gpt-5.3-chat,Kimi-K2.5",
                "OPENCLAW_DEFAULT_OPENAI_MODEL": "Kimi-K2.5",
                "OPENCLAW_IMAGE": "ghcr.io/example/openclaw",
                "OPENCLAW_IMAGE_TAG": "1.0.0",
            },
            clear=False,
        ):
            status = ensure_container_exists(docker, identity="u1001", container="openclaw-u1001")
            self.assertEqual(status, "created")
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            provider = cfg.get("models", {}).get("providers", {}).get("openai", {})
            self.assertEqual(provider.get("api"), "openai-responses")
            reasoning_map = {m.get("id"): m.get("reasoning") for m in provider.get("models", [])}
            self.assertEqual(reasoning_map.get("gpt-5.2"), True)
            self.assertEqual(reasoning_map.get("gpt-5.3-chat"), False)
            self.assertEqual(reasoning_map.get("Kimi-K2.5"), False)

    def test_default_model_falls_back_to_dashscope_minimax_when_env_missing(self):
        docker = FakeDocker()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "k-test",
                "OPENCLAW_DEFAULT_OPENAI_ENDPOINT": "https://api.openai.com/v1",
                "OPENCLAW_DASHSCOPE_API_KEY": "dashscope-test-key",
                "OPENCLAW_IMAGE": "ghcr.io/example/openclaw",
                "OPENCLAW_IMAGE_TAG": "1.0.0",
            },
            clear=False,
        ):
            os.environ.pop("OPENCLAW_DEFAULT_OPENAI_MODEL", None)
            os.environ.pop("OPENCLAW_ALLOWED_MODELS", None)
            status = ensure_container_exists(docker, identity="u1009", container="openclaw-u1009")
            self.assertEqual(status, "created")
            with open(f"{tmpdir}/u1009/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertEqual(
                cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary"),
                "dashscope/MiniMax-M2.5",
            )
            providers = cfg.get("models", {}).get("providers", {})
            self.assertIn("openai", providers)
            self.assertIn("dashscope", providers)
            openai_ids = [model.get("id") for model in providers.get("openai", {}).get("models", [])]
            self.assertIn("gpt-5.4", openai_ids)
            self.assertIn("gpt-5.3-codex", openai_ids)
            self.assertIn("gpt-5.3-chat", openai_ids)
            dashscope_ids = [model.get("id") for model in providers.get("dashscope", {}).get("models", [])]
            self.assertEqual(
                dashscope_ids,
                ["MiniMax-M2.5", "kimi-k2.5", "deepseek-v3.2", "qwen3.5-flash"],
            )

    def test_adds_dashscope_provider_with_expected_defaults(self):
        docker = FakeDocker()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "k-test",
                "OPENCLAW_DEFAULT_OPENAI_ENDPOINT": "https://api.openai.com/v1",
                "OPENCLAW_DASHSCOPE_API_KEY": "dashscope-test-key",
                "OPENCLAW_IMAGE": "ghcr.io/example/openclaw",
                "OPENCLAW_IMAGE_TAG": "1.0.0",
            },
            clear=False,
        ):
            os.environ.pop("OPENCLAW_DEFAULT_OPENAI_MODEL", None)
            os.environ.pop("OPENCLAW_ALLOWED_MODELS", None)
            status = ensure_container_exists(docker, identity="u1010", container="openclaw-u1010")
            self.assertEqual(status, "created")
            with open(f"{tmpdir}/u1010/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            dashscope = cfg.get("models", {}).get("providers", {}).get("dashscope", {})
            self.assertEqual(dashscope.get("api"), "openai-completions")
            self.assertEqual(dashscope.get("apiKey"), "dashscope-test-key")
            self.assertEqual(
                dashscope.get("baseUrl"),
                "https://dashscope-yxai.hatch.yinxiang.com/compatible-mode/v1",
            )
            params = cfg.get("agents", {}).get("defaults", {}).get("models", {}).get("dashscope/MiniMax-M2.5", {}).get("params", {})
            self.assertEqual(params.get("transport"), "sse")
            self.assertEqual(params.get("openaiWsWarmup"), False)

    def test_migrates_existing_managed_primary_to_dashscope_default(self):
        docker = FakeDocker()
        docker.existing.add("openclaw-u1012")
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "k-test",
                "OPENCLAW_DEFAULT_OPENAI_ENDPOINT": "https://api.openai.com/v1",
                "OPENCLAW_DEFAULT_OPENAI_MODEL": "dashscope/MiniMax-M2.5",
                "OPENCLAW_DASHSCOPE_API_KEY": "dashscope-test-key",
            },
            clear=False,
        ):
            os.makedirs(f"{tmpdir}/u1012/runtime", exist_ok=True)
            with open(f"{tmpdir}/u1012/runtime/openclaw.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "agents": {
                            "defaults": {
                                "model": {"primary": "openai/gpt-5.4"},
                            }
                        }
                    },
                    f,
                )
            status = ensure_container_exists(docker, identity="u1012", container="openclaw-u1012")
            self.assertEqual(status, "existing")
            with open(f"{tmpdir}/u1012/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertEqual(
                cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary"),
                "dashscope/MiniMax-M2.5",
            )

    def test_skips_dashscope_provider_when_key_missing(self):
        docker = FakeDocker()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "k-test",
                "OPENCLAW_DEFAULT_OPENAI_ENDPOINT": "https://api.openai.com/v1",
                "OPENCLAW_IMAGE": "ghcr.io/example/openclaw",
                "OPENCLAW_IMAGE_TAG": "1.0.0",
            },
            clear=False,
        ):
            os.environ.pop("OPENCLAW_DEFAULT_OPENAI_MODEL", None)
            os.environ.pop("OPENCLAW_ALLOWED_MODELS", None)
            os.environ.pop("OPENCLAW_DASHSCOPE_API_KEY", None)
            status = ensure_container_exists(docker, identity="u1011", container="openclaw-u1011")
            self.assertEqual(status, "created")
            with open(f"{tmpdir}/u1011/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertEqual(
                cfg.get("agents", {}).get("defaults", {}).get("model", {}).get("primary"),
                "openai/gpt-5.3-chat",
            )
            self.assertNotIn("dashscope", cfg.get("models", {}).get("providers", {}))

    def test_honors_openai_api_override(self):
        docker = FakeDocker()
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "k-test",
                "OPENCLAW_DEFAULT_OPENAI_ENDPOINT": "https://api.openai.com/v1",
                "OPENCLAW_ALLOWED_MODELS": "gpt-5.2,gpt-5.3-chat,Kimi-K2.5",
                "OPENCLAW_DEFAULT_OPENAI_MODEL": "gpt-5.2",
                "OPENCLAW_OPENAI_API": "openai-completions",
                "OPENCLAW_IMAGE": "ghcr.io/example/openclaw",
                "OPENCLAW_IMAGE_TAG": "1.0.0",
            },
            clear=False,
        ):
            status = ensure_container_exists(docker, identity="u1002", container="openclaw-u1002")
            self.assertEqual(status, "created")
            with open(f"{tmpdir}/u1002/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            provider = cfg.get("models", {}).get("providers", {}).get("openai", {})
            self.assertEqual(provider.get("api"), "openai-completions")
            self.assertTrue(all(m.get("reasoning") is False for m in provider.get("models", [])))
            _, spec = docker.created[0]
            cmd = spec.get("Cmd", [])
            self.assertEqual(cmd[:2], ["sh", "-lc"])
            self.assertNotIn("/opt/openclaw/extensions", cmd[2] if len(cmd) > 2 else "")
            self.assertIn("/app/extensions", cmd[2] if len(cmd) > 2 else "")
            self.assertIn("channels[channelId]", cmd[2] if len(cmd) > 2 else "")

    def test_extra_channel_plugins_default_enabled_also_enable_channel_config(self):
        docker = FakeDocker()
        docker.existing.add("openclaw-u1001")
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "",
                "OPENCLAW_DEFAULT_CHANNEL_PLUGINS": "wecom",
            },
            clear=False,
        ):
            os.makedirs(f"{tmpdir}/u1001/runtime", exist_ok=True)
            status = ensure_container_exists(docker, identity="u1001", container="openclaw-u1001")
            self.assertEqual(status, "existing")
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertTrue(cfg.get("plugins", {}).get("entries", {}).get("wecom", {}).get("enabled"))
            self.assertTrue(cfg.get("channels", {}).get("wecom", {}).get("enabled"))

    def test_channel_plugins_default_enabled_without_overriding_explicit_false(self):
        docker = FakeDocker()
        docker.existing.add("openclaw-u1001")
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "",
                "OPENCLAW_DEFAULT_CHANNEL_PLUGINS": "telegram,wecom",
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
            self.assertTrue(cfg.get("plugins", {}).get("entries", {}).get("wecom", {}).get("enabled"))

    def test_discovered_channel_plugins_default_enabled_without_overriding_explicit_false(self):
        docker = FakeDocker()
        docker.existing.add("openclaw-u1001")
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as plugdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "",
                "OPENCLAW_DEFAULT_CHANNEL_PLUGINS": "telegram",
                "OPENCLAW_DEFAULT_CHANNEL_PLUGIN_DIRS": plugdir,
            },
            clear=False,
        ):
            os.makedirs(f"{tmpdir}/u1001/runtime", exist_ok=True)
            os.makedirs(os.path.join(plugdir, "discord"), exist_ok=True)
            os.makedirs(os.path.join(plugdir, "wecom"), exist_ok=True)
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
            entries = cfg.get("plugins", {}).get("entries", {})
            self.assertFalse(entries.get("telegram", {}).get("enabled"))
            self.assertTrue(entries.get("discord", {}).get("enabled"))
            self.assertTrue(entries.get("wecom", {}).get("enabled"))


    def test_discovered_channel_plugin_ids_prefer_manifest_channel_id(self):
        with tempfile.TemporaryDirectory() as plugdir:
            plugin_dir = os.path.join(plugdir, "wecom-openclaw-plugin")
            os.makedirs(plugin_dir, exist_ok=True)
            with open(os.path.join(plugin_dir, "package.json"), "w", encoding="utf-8") as f:
                json.dump({"openclaw": {"channel": {"id": "wecom"}}}, f)
            discovered = instance_manager_main._discover_channel_plugin_ids([plugdir])
            self.assertEqual(discovered, ["wecom"])

    def test_discovered_channel_plugin_ids_fall_back_to_directory_name(self):
        with tempfile.TemporaryDirectory() as plugdir:
            plugin_dir = os.path.join(plugdir, "discord")
            os.makedirs(plugin_dir, exist_ok=True)
            with open(os.path.join(plugin_dir, "package.json"), "w", encoding="utf-8") as f:
                json.dump({"name": "discord-plugin"}, f)
            discovered = instance_manager_main._discover_channel_plugin_ids([plugdir])
            self.assertEqual(discovered, ["discord"])

    def test_invalid_discovered_plugin_names_are_ignored(self):
        docker = FakeDocker()
        docker.existing.add("openclaw-u1001")
        with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as plugdir, patch.dict(
            os.environ,
            {
                "OPENCLAW_USERS_ROOT": tmpdir,
                "OPENCLAW_DEFAULT_OPENAI_KEY": "",
                "OPENCLAW_DEFAULT_CHANNEL_PLUGIN_DIRS": plugdir,
            },
            clear=False,
        ):
            os.makedirs(f"{tmpdir}/u1001/runtime", exist_ok=True)
            os.makedirs(os.path.join(plugdir, "good-plugin"), exist_ok=True)
            os.makedirs(os.path.join(plugdir, "Bad Plugin"), exist_ok=True)
            status = ensure_container_exists(docker, identity="u1001", container="openclaw-u1001")
            self.assertEqual(status, "existing")
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            entries = cfg.get("plugins", {}).get("entries", {})
            self.assertIn("good-plugin", entries)
            self.assertNotIn("Bad Plugin", entries)

    def test_dockerfile_uses_base_entrypoint_directly(self):
        dockerfile = Path("/home/fyue/git/clawpool/infra/docker-build/Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("docker-entrypoint-with-extensions.sh", dockerfile)
        self.assertIn('ENTRYPOINT ["docker-entrypoint.sh"]', dockerfile)

    def test_default_startup_cmd_installs_runtime_compatibility_shims(self):
        cmd = _build_default_startup_cmd()
        self.assertEqual(cmd[:2], ["sh", "-lc"])
        script = cmd[2]
        self.assertIn("parse-finite-number.js", script)
        self.assertIn("abort-signal.js", script)
        self.assertIn("waitForAbortSignal", script)
        self.assertIn("parseStrictPositiveInteger", script)

    def test_default_startup_cmd_reconciles_built_in_channels_and_extra_plugins(self):
        cmd = _build_default_startup_cmd()
        self.assertEqual(cmd[:2], ["sh", "-lc"])
        script = cmd[2]
        self.assertNotIn("/opt/openclaw/extensions", script)
        self.assertIn("/app/extensions", script)
        self.assertIn("channel.ts", script)
        self.assertIn("channel && validPluginId(channel.id) ? channel.id : entry.name", script)
        self.assertIn("OPENCLAW_DEFAULT_CHANNEL_PLUGIN_DIRS", script)
        self.assertIn("openclaw.json", script)
        self.assertIn("plugins.entries", script)
        self.assertIn("channels[channelId]", script)
        self.assertIn("delete cfg.plugins.entries[channelId]", script)
        self.assertIn("plugins.allow", script)
        self.assertNotIn("plugins.load.paths", script)
        self.assertIn("createRequire", script)
        self.assertIn("package.json", script)
        self.assertIn("dependencies", script)
        self.assertIn("index.ts", script)
        self.assertIn("registerChannel(", script)
        self.assertIn("channel plugin", script)
        self.assertIn("loadableBuiltInChannelIds", script)
        self.assertIn("delete cfg.channels[channelId]", script)
        self.assertIn("cfg.plugins.allow = cfg.plugins.allow.filter", script)
        self.assertNotIn("cfg.plugins.load.paths = cfg.plugins.load.paths.filter", script)
        self.assertNotIn("fs.existsSync(pluginPath.trim())", script)
        self.assertIn("!allBuiltInChannelIds.includes(pluginId)", script)
        self.assertIn("enabled = true", script)
        self.assertIn("\\n", script)

    def test_custom_startup_cmd_still_runs_plugin_reconciliation(self):
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
                "OPENCLAW_STARTUP_CMD": "node custom-entry.mjs",
            },
            clear=False,
        ):
            status = ensure_container_exists(docker, identity="u1003", container="openclaw-u1003")
            self.assertEqual(status, "created")
            _, spec = docker.created[0]
            cmd = spec.get("Cmd", [])
            self.assertEqual(cmd[:2], ["sh", "-lc"])
            script = cmd[2] if len(cmd) > 2 else ""
            self.assertNotIn("/opt/openclaw/extensions", script)
            self.assertIn("/app/extensions", script)
            self.assertIn("channels[channelId]", script)
            self.assertIn("openclaw.json", script)
            self.assertIn("exec node custom-entry.mjs", script)

    def test_webchat_file_upload_default_enabled_without_overriding_explicit_false(self):
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
            os.makedirs(f"{tmpdir}/u1001/runtime", exist_ok=True)
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "tools": {
                            "media": {
                                "image": {
                                    "enabled": False,
                                }
                            }
                        }
                    },
                    f,
                )
            status = ensure_container_exists(docker, identity="u1001", container="openclaw-u1001")
            self.assertEqual(status, "existing")
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertFalse(cfg.get("tools", {}).get("media", {}).get("image", {}).get("enabled"))

    def test_tools_profile_and_sessions_visibility_are_forced(self):
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
            os.makedirs(f"{tmpdir}/u1001/runtime", exist_ok=True)
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "tools": {
                            "profile": "minimal",
                            "sessions": {
                                "visibility": "private",
                            },
                        }
                    },
                    f,
                )
            status = ensure_container_exists(docker, identity="u1001", container="openclaw-u1001")
            self.assertEqual(status, "existing")
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertEqual(cfg.get("tools", {}).get("profile"), "full")
            self.assertEqual(
                cfg.get("tools", {}).get("sessions", {}).get("visibility"),
                "all",
            )

    def test_browser_defaults_without_overriding_explicit_values(self):
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
            os.makedirs(f"{tmpdir}/u1001/runtime", exist_ok=True)
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "browser": {
                            "executablePath": "/custom/chrome",
                            "headless": False,
                            "noSandbox": False,
                        }
                    },
                    f,
                )
            status = ensure_container_exists(docker, identity="u1001", container="openclaw-u1001")
            self.assertEqual(status, "existing")
            with open(f"{tmpdir}/u1001/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertEqual(cfg.get("browser", {}).get("executablePath"), "/custom/chrome")
            self.assertFalse(cfg.get("browser", {}).get("headless"))
            self.assertFalse(cfg.get("browser", {}).get("noSandbox"))

    def test_webchat_file_upload_default_enabled_when_missing(self):
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
            status = ensure_container_exists(docker, identity="u1002", container="openclaw-u1002")
            self.assertEqual(status, "created")
            with open(f"{tmpdir}/u1002/runtime/openclaw.json", "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.assertTrue(cfg.get("tools", {}).get("media", {}).get("image", {}).get("enabled"))

    def test_disables_default_store_patch_startup_cmd_when_flag_off(self):
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
                "OPENCLAW_FORCE_RESPONSES_STORE": "false",
            },
            clear=False,
        ):
            status = ensure_container_exists(docker, identity="u1003", container="openclaw-u1003")
            self.assertEqual(status, "created")
            _, spec = docker.created[0]
            cmd = spec.get("Cmd", [])
            self.assertEqual(cmd[:2], ["sh", "-lc"])
            script = cmd[2] if len(cmd) > 2 else ""
            self.assertNotIn("/opt/openclaw/extensions", script)
            self.assertIn("/app/extensions", script)
            self.assertIn("channels[channelId]", script)
            self.assertNotIn("store: true", script)

    def test_custom_startup_cmd_overrides_default_store_patch(self):
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
                "OPENCLAW_FORCE_RESPONSES_STORE": "true",
                "OPENCLAW_STARTUP_CMD": "node openclaw.mjs gateway --allow-unconfigured",
            },
            clear=False,
        ):
            status = ensure_container_exists(docker, identity="u1004", container="openclaw-u1004")
            self.assertEqual(status, "created")
            _, spec = docker.created[0]
            cmd = spec.get("Cmd", [])
            self.assertEqual(cmd[:2], ["sh", "-lc"])
            script = cmd[2] if len(cmd) > 2 else ""
            self.assertNotIn("/opt/openclaw/extensions", script)
            self.assertIn("/app/extensions", script)
            self.assertIn("channels[channelId]", script)
            self.assertIn("store: true", script)
            self.assertIn("exec node openclaw.mjs gateway --allow-unconfigured", script)

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

    def test_synthesizes_local_pairing_from_device_identity_without_pending(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime_dir = f"{tmpdir}/runtime"
            os.makedirs(f"{runtime_dir}/identity", exist_ok=True)
            os.makedirs(f"{runtime_dir}/devices", exist_ok=True)
            with open(f"{runtime_dir}/identity/device.json", "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "version": 1,
                        "deviceId": "dev-3",
                        "publicKeyPem": "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEALjkylneJBf72gsY1K5962v1I5C3jjOCTeakT9rKS+ho=\n-----END PUBLIC KEY-----\n",
                    },
                    f,
                )

            instance_manager_main._repair_local_device_pairing(runtime_dir, 1000, 1000)

            with open(f"{runtime_dir}/devices/paired.json", "r", encoding="utf-8") as f:
                paired = json.load(f)

            self.assertIn("dev-3", paired)
            self.assertEqual(paired.get("dev-3", {}).get("role"), "operator")
            self.assertEqual(
                paired.get("dev-3", {}).get("publicKey"),
                "LjkylneJBf72gsY1K5962v1I5C3jjOCTeakT9rKS-ho",
            )
            for required in [
                "operator.admin",
                "operator.read",
                "operator.write",
                "operator.approvals",
                "operator.pairing",
            ]:
                self.assertIn(required, paired.get("dev-3", {}).get("scopes", []))
                self.assertIn(
                    required,
                    paired.get("dev-3", {}).get("tokens", {}).get("operator", {}).get("scopes", []),
                )


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
