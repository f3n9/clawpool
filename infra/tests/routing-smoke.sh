#!/usr/bin/env bash
set -euo pipefail

FILE="infra/traefik/dynamic.yml"

[[ -f "$FILE" ]] || { echo "FAIL: missing dynamic routing file"; exit 1; }
grep -q "http://oauth2-proxy:4180" "$FILE" || { echo "FAIL: missing oauth2-proxy upstream service"; exit 1; }
grep -q 'Host(' "$FILE" || { echo "FAIL: missing host rule"; exit 1; }
grep -q 'PathPrefix(`/`)' "$FILE" || { echo "FAIL: missing root path routing"; exit 1; }

echo "PASS: dynamic routing baseline is configured"
