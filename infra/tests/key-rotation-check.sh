#!/usr/bin/env bash
set -euo pipefail

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

ROOT_DIR="$TMP_ROOT/users"
OPENCLAW_DEFAULT_OPENAI_KEY="default-test-key" ROOT_DIR="$ROOT_DIR" bash infra/scripts/provision-users.sh infra/users.csv
OPENCLAW_DEFAULT_OPENAI_KEY="default-test-key" ROOT_DIR="$ROOT_DIR" bash infra/scripts/provision-user-secrets.sh infra/users.csv

bash infra/scripts/rotate-user-openai-key.sh u1001 new-key-1001 "$ROOT_DIR"
[[ "$(cat "$ROOT_DIR/u1001/secrets/openai_api_key")" == "new-key-1001" ]] || { echo "FAIL: u1001 key not rotated"; exit 1; }
[[ "$(cat "$ROOT_DIR/u1002/secrets/openai_api_key")" == "default-test-key" ]] || { echo "FAIL: u1002 key should remain default"; exit 1; }

set +e
OPENCLAW_DEFAULT_OPENAI_KEY="default-test-key" ROOT_DIR="$ROOT_DIR" bash infra/scripts/audit-default-keys.sh
code=$?
set -e
[[ "$code" -eq 2 ]] || { echo "FAIL: expected audit to detect default key usage"; exit 1; }

bash infra/scripts/rotate-user-openai-key.sh u1002 new-key-1002 "$ROOT_DIR"
bash infra/scripts/rotate-user-openai-key.sh u1003 new-key-1003 "$ROOT_DIR"
OPENCLAW_DEFAULT_OPENAI_KEY="default-test-key" ROOT_DIR="$ROOT_DIR" bash infra/scripts/audit-default-keys.sh

echo "PASS: key rotation and default-key audit work"
