#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <employee_id> <new_key> [root_dir]"
  exit 1
fi

EMPLOYEE_ID="$1"
NEW_KEY="$2"
ROOT_DIR="${3:-${ROOT_DIR:-/srv/openclaw/users}}"

secret_file="$ROOT_DIR/$EMPLOYEE_ID/secrets/openai_api_key"
[[ -f "$secret_file" ]] || { echo "missing secret file: $secret_file"; exit 1; }

printf '%s\n' "$NEW_KEY" > "$secret_file"
chmod 600 "$secret_file"

echo "Rotated OpenAI key for $EMPLOYEE_ID"
if command -v docker >/dev/null 2>&1; then
  docker restart "openclaw-$EMPLOYEE_ID" >/dev/null 2>&1 || true
fi
