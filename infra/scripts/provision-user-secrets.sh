#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/srv/openclaw/users}"
CSV_FILE="${1:-infra/users.csv}"
DEFAULT_KEY="${OPENCLAW_DEFAULT_OPENAI_KEY:-}"
DEFAULT_ENDPOINT="${OPENCLAW_DEFAULT_OPENAI_ENDPOINT:-https://api.openai.com/v1}"
ALLOWED_MODELS="${OPENCLAW_ALLOWED_MODELS:-gpt-5.4,gpt-5.3-codex,gpt-5.3-chat}"
DEFAULT_MODEL="${OPENCLAW_DEFAULT_OPENAI_MODEL:-gpt-5.3-chat}"

[[ -f "$CSV_FILE" ]] || { echo "missing users csv: $CSV_FILE"; exit 1; }
[[ -n "$DEFAULT_KEY" ]] || { echo "OPENCLAW_DEFAULT_OPENAI_KEY is required"; exit 1; }
if [[ ",$ALLOWED_MODELS," != *",$DEFAULT_MODEL,"* ]]; then
  echo "OPENCLAW_DEFAULT_OPENAI_MODEL ($DEFAULT_MODEL) must be in OPENCLAW_ALLOWED_MODELS ($ALLOWED_MODELS)"
  exit 1
fi

tail -n +2 "$CSV_FILE" | while IFS=, read -r employee_id; do
  [[ -n "${employee_id}" ]] || continue
  secret_file="$ROOT_DIR/$employee_id/secrets/openai_api_key"
  endpoint_file="$ROOT_DIR/$employee_id/secrets/openai_endpoint"
  model_file="$ROOT_DIR/$employee_id/secrets/openai_model"
  if [[ ! -f "$secret_file" ]]; then
    mkdir -p "$(dirname "$secret_file")"
    printf '%s\n' "$DEFAULT_KEY" > "$secret_file"
    chmod 600 "$secret_file"
  fi
  if [[ ! -f "$endpoint_file" ]]; then
    mkdir -p "$(dirname "$endpoint_file")"
    printf '%s\n' "$DEFAULT_ENDPOINT" > "$endpoint_file"
    chmod 600 "$endpoint_file"
  fi
  if [[ ! -f "$model_file" ]]; then
    mkdir -p "$(dirname "$model_file")"
    printf '%s\n' "$DEFAULT_MODEL" > "$model_file"
    chmod 600 "$model_file"
  fi
done

echo "Provisioned default OpenAI key, endpoint, and model for users"
