#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/srv/openclaw/users}"
CSV_FILE="${1:-infra/users.csv}"
DEFAULT_KEY="${OPENCLAW_DEFAULT_OPENAI_KEY:-}"

[[ -f "$CSV_FILE" ]] || { echo "missing users csv: $CSV_FILE"; exit 1; }
[[ -n "$DEFAULT_KEY" ]] || { echo "OPENCLAW_DEFAULT_OPENAI_KEY is required"; exit 1; }

tail -n +2 "$CSV_FILE" | while IFS=, read -r employee_id; do
  [[ -n "${employee_id}" ]] || continue
  secret_file="$ROOT_DIR/$employee_id/secrets/openai_api_key"
  if [[ ! -f "$secret_file" ]]; then
    mkdir -p "$(dirname "$secret_file")"
    printf '%s\n' "$DEFAULT_KEY" > "$secret_file"
    chmod 600 "$secret_file"
  fi
done

echo "Provisioned default OpenAI keys for users"
