#!/usr/bin/env bash
set -euo pipefail

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

ROOT_DIR="$TMP_ROOT/users"
OPENCLAW_DEFAULT_OPENAI_KEY="default-test-key" ROOT_DIR="$ROOT_DIR" bash infra/scripts/provision-users.sh infra/users.csv
OPENCLAW_DEFAULT_OPENAI_KEY="default-test-key" \
OPENCLAW_ALLOWED_MODELS="gpt-5.4,gpt-5.3-codex,gpt-5.3-chat" \
OPENCLAW_DEFAULT_OPENAI_MODEL="gpt-5.3-codex" \
ROOT_DIR="$ROOT_DIR" \
bash infra/scripts/provision-user-secrets.sh infra/users.csv

[[ "$(cat "$ROOT_DIR/u1001/secrets/openai_model")" == "gpt-5.3-codex" ]] || { echo "FAIL: expected u1001 model gpt-5.3-codex"; exit 1; }
[[ "$(cat "$ROOT_DIR/u1002/secrets/openai_model")" == "gpt-5.3-codex" ]] || { echo "FAIL: expected u1002 model gpt-5.3-codex"; exit 1; }

set +e
OPENCLAW_DEFAULT_OPENAI_KEY="default-test-key" \
OPENCLAW_ALLOWED_MODELS="gpt-5.4,gpt-5.3-codex,gpt-5.3-chat" \
OPENCLAW_DEFAULT_OPENAI_MODEL="gpt-4.1" \
ROOT_DIR="$ROOT_DIR" \
bash infra/scripts/provision-user-secrets.sh infra/users.csv >/dev/null 2>&1
code=$?
set -e
[[ "$code" -ne 0 ]] || { echo "FAIL: expected invalid model policy to fail"; exit 1; }

echo "PASS: model whitelist policy is enforced"
