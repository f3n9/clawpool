#!/usr/bin/env bash
set -euo pipefail

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

mkdir -p "$TMP_ROOT/u1001/data" "$TMP_ROOT/u1001/config"
echo "hello" > "$TMP_ROOT/u1001/data/chat.log"
echo "channel: wecom" > "$TMP_ROOT/u1001/config/settings.yml"

bash ops/upgrade-openclaw.sh test-tag infra/docker-compose.base.yml

[[ "$(cat "$TMP_ROOT/u1001/data/chat.log")" == "hello" ]] || { echo "FAIL: data not retained"; exit 1; }
[[ "$(cat "$TMP_ROOT/u1001/config/settings.yml")" == "channel: wecom" ]] || { echo "FAIL: config not retained"; exit 1; }

echo "PASS: upgrade workflow preserves existing data/config"
