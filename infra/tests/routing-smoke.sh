#!/usr/bin/env bash
set -euo pipefail

FILE="infra/traefik/dynamic.yml"

[[ -f "$FILE" ]] || { echo "FAIL: missing dynamic routing file"; exit 1; }
grep -q "oauth-forward-auth" "$FILE" || { echo "FAIL: missing auth middleware"; exit 1; }
grep -q "instance-manager" "$FILE" || { echo "FAIL: missing instance-manager upstream"; exit 1; }

echo "PASS: dynamic routing baseline is configured"
