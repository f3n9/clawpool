#!/usr/bin/env bash
set -euo pipefail

USER_EMAIL="${1:-}"
HOST="${2:-}"

if [[ -z "$USER_EMAIL" ]]; then
  echo "Usage: $0 <user_email> [host]" >&2
  exit 2
fi

if [[ -z "$HOST" && -f "infra/.env" ]]; then
  HOST="$(grep -E '^OPENCLAW_HOST=' infra/.env | tail -n 1 | cut -d'=' -f2-)"
fi

if [[ -z "$HOST" ]]; then
  echo "FAIL: missing host argument and OPENCLAW_HOST is not set in infra/.env" >&2
  exit 1
fi

NORM_ID="$(
python3 - "$USER_EMAIL" <<'PY'
import re
import sys

raw = (sys.argv[1] or "").strip().lower()
safe = re.sub(r"[^a-z0-9._-]+", "-", raw)
safe = re.sub(r"-{2,}", "-", safe).strip("-.")
print(safe[:96])
PY
)"

if [[ -z "$NORM_ID" ]]; then
  echo "FAIL: normalized identity is empty for input '$USER_EMAIL'" >&2
  exit 1
fi

RUNTIME_CFG="/srv/openclaw/users/${NORM_ID}/runtime/openclaw.json"
EXPECTED_CONTAINER="openclaw-${NORM_ID}"

echo "Checking services..."
docker inspect -f '{{.State.Running}}' infra-instance-manager-1 | grep -qx 'true' || {
  echo "FAIL: infra-instance-manager-1 is not running" >&2
  exit 1
}
docker inspect -f '{{.State.Running}}' infra-oauth2-proxy-1 | grep -qx 'true' || {
  echo "FAIL: infra-oauth2-proxy-1 is not running" >&2
  exit 1
}

echo "Checking runtime config: ${RUNTIME_CFG}"
[[ -f "$RUNTIME_CFG" ]] || {
  echo "FAIL: runtime config not found: ${RUNTIME_CFG}" >&2
  exit 1
}

python3 - "$RUNTIME_CFG" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    cfg = json.load(f)

gateway = cfg.get("gateway", {})
auth = gateway.get("auth", {})
trusted = auth.get("trustedProxy", {})
trusted_proxies = gateway.get("trustedProxies", [])

assert auth.get("mode") == "trusted-proxy", "gateway.auth.mode != trusted-proxy"
assert isinstance(trusted.get("userHeader"), str) and trusted.get("userHeader").strip(), "missing trustedProxy.userHeader"
assert "emailHeader" not in trusted, "invalid key trustedProxy.emailHeader is present"
assert "cidrs" not in trusted, "invalid key trustedProxy.cidrs is present"
assert isinstance(trusted_proxies, list) and len(trusted_proxies) > 0, "gateway.trustedProxies is empty"
PY

echo "Checking /resolve routing..."
RESOLVE_OUT="$(
docker exec -i infra-instance-manager-1 python - "$USER_EMAIL" <<'PY'
import http.client
import json
import sys

u = sys.argv[1]
c = http.client.HTTPConnection("127.0.0.1", 8080, timeout=30)
c.request("GET", f"/resolve?employee_id={u}", headers={"X-Request-Id": "health-check"})
r = c.getresponse()
body = r.read().decode()
print(json.dumps({"status": r.status, "body": body}))
PY
)"

python3 - "$RESOLVE_OUT" "$EXPECTED_CONTAINER" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
status = payload["status"]
body = json.loads(payload["body"])
expected = sys.argv[2]

assert status == 200, f"/resolve returned status={status}"
assert body.get("container") == expected, f"unexpected container: {body.get('container')} != {expected}"
assert body.get("state") == "ready", f"unexpected state: {body.get('state')}"
PY

echo "Checking websocket 101 upgrade..."
WS_STATUS="$(
docker exec -i infra-instance-manager-1 python - "$USER_EMAIL" "$HOST" <<'PY'
import base64
import os
import socket
import sys

user = sys.argv[1]
host = sys.argv[2]
key = base64.b64encode(os.urandom(16)).decode()
s = socket.create_connection(("127.0.0.1", 8080), timeout=20)
req = (
    "GET / HTTP/1.1\r\n"
    f"Host: {host}\r\n"
    "Connection: Upgrade\r\n"
    "Upgrade: websocket\r\n"
    "Sec-WebSocket-Version: 13\r\n"
    f"Sec-WebSocket-Key: {key}\r\n"
    f"Origin: https://{host}\r\n"
    f"X-Forwarded-Email: {user}\r\n"
    f"X-Forwarded-User: {user}\r\n"
    "\r\n"
)
s.sendall(req.encode())
line = s.recv(1024).decode(errors="ignore").split("\r\n")[0]
s.close()
print(line)
PY
)"

echo "${WS_STATUS}" | grep -q "101 Switching Protocols" || {
  echo "FAIL: websocket upgrade failed, status='${WS_STATUS}'" >&2
  exit 1
}

echo "PASS: gateway health check passed for ${USER_EMAIL} (${EXPECTED_CONTAINER})"
