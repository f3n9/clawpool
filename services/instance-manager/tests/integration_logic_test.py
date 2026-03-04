import unittest

from services_instance_manager.main import (
    DockerAPIClient,
    extract_identity,
    resolve_container_name,
    start_container_if_needed,
)


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        key = (method, path)
        if key not in self.responses:
            raise RuntimeError(f"unexpected call: {key}")
        return self.responses[key]


class IntegrationLogicTests(unittest.TestCase):
    def test_extract_identity_prefers_employee_header(self):
        headers = {
            "X-Employee-Id": "u1001",
            "X-Auth-Request-User": "fallback-user",
        }
        self.assertEqual(extract_identity(headers), ("u1001", None))

    def test_extract_identity_falls_back_to_oauth_user(self):
        headers = {
            "X-Auth-Request-User": "user-from-oauth2-proxy",
        }
        self.assertEqual(extract_identity(headers), ("user-from-oauth2-proxy", None))

    def test_start_container_if_needed_uses_docker_api(self):
        transport = FakeTransport(
            {
                ("GET", "/containers/openclaw-u1001/json"): {
                    "State": {"Running": False, "Health": {"Status": "starting"}}
                },
                ("POST", "/containers/openclaw-u1001/start"): None,
                ("GET", "/containers/openclaw-u1001/json?wait=1"): {
                    "State": {"Running": True, "Health": {"Status": "healthy"}}
                },
            }
        )
        client = DockerAPIClient(transport=transport)
        state = start_container_if_needed(client, "openclaw-u1001", health_timeout_seconds=1)
        self.assertEqual(state, "started")

    def test_resolve_container_name_accepts_oauth_fallback(self):
        container = resolve_container_name(
            employee_id="user-from-oauth2-proxy", user_sub=None, mapping={}
        )
        self.assertEqual(container, "openclaw-user-from-oauth2-proxy")


if __name__ == "__main__":
    unittest.main()
