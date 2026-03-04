#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/srv/openclaw/users}"
DEFAULT_KEY="${OPENCLAW_DEFAULT_OPENAI_KEY:-}"

[[ -n "$DEFAULT_KEY" ]] || { echo "OPENCLAW_DEFAULT_OPENAI_KEY is required"; exit 1; }

violations=0
for key_file in "$ROOT_DIR"/*/secrets/openai_api_key; do
  [[ -f "$key_file" ]] || continue
  value="$(cat "$key_file")"
  if [[ "$value" == "$DEFAULT_KEY" ]]; then
    echo "DEFAULT_KEY_IN_USE: $key_file"
    violations=$((violations + 1))
  fi
done

if [[ "$violations" -gt 0 ]]; then
  exit 2
fi

echo "PASS: no instance uses default key"
