#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="${ROOT_DIR:-/srv/openclaw/users}"
CSV_FILE="${1:-infra/users.csv}"

[[ -f "$CSV_FILE" ]] || { echo "missing users csv: $CSV_FILE"; exit 1; }
mkdir -p "$ROOT_DIR"

tail -n +2 "$CSV_FILE" | while IFS=, read -r employee_id; do
  [[ -n "${employee_id}" ]] || continue
  base="$ROOT_DIR/$employee_id"
  mkdir -p "$base/data" "$base/config" "$base/secrets"
  chmod 700 "$base" "$base/data" "$base/config" "$base/secrets"
done

echo "Provisioned user directories under $ROOT_DIR"
