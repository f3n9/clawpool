#!/usr/bin/env bash
set -euo pipefail

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

ROOT_DIR="$TMP_ROOT/users"
OPENCLAW_DEFAULT_OPENAI_KEY="default-test-key" ROOT_DIR="$ROOT_DIR" bash infra/scripts/provision-users.sh infra/users.csv
OPENCLAW_DEFAULT_OPENAI_KEY="default-test-key" ROOT_DIR="$ROOT_DIR" bash infra/scripts/provision-user-secrets.sh infra/users.csv

for user in u1001 u1002; do
  [[ -d "$ROOT_DIR/$user/data" ]] || { echo "FAIL: missing $user data dir"; exit 1; }
  [[ -d "$ROOT_DIR/$user/config" ]] || { echo "FAIL: missing $user config dir"; exit 1; }
  [[ -f "$ROOT_DIR/$user/secrets/openai_api_key" ]] || { echo "FAIL: missing $user key"; exit 1; }

  dir_mode=$(stat -f %Mp%Lp "$ROOT_DIR/$user")
  key_mode=$(stat -f %Mp%Lp "$ROOT_DIR/$user/secrets/openai_api_key")
  [[ "$dir_mode" == "700" || "$dir_mode" == "0700" ]] || { echo "FAIL: $user dir mode is $dir_mode"; exit 1; }
  [[ "$key_mode" == "600" || "$key_mode" == "0600" ]] || { echo "FAIL: $user key mode is $key_mode"; exit 1; }
done

u1_key=$(cat "$ROOT_DIR/u1001/secrets/openai_api_key")
u2_key=$(cat "$ROOT_DIR/u1002/secrets/openai_api_key")
[[ "$u1_key" == "default-test-key" ]] || { echo "FAIL: u1001 key not initialized"; exit 1; }
[[ "$u2_key" == "default-test-key" ]] || { echo "FAIL: u1002 key not initialized"; exit 1; }

echo "PASS: isolation baseline directories and secrets are provisioned"
