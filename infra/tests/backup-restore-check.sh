#!/usr/bin/env bash
set -euo pipefail

TMP_SRC="$(mktemp -d)"
TMP_OUT="$(mktemp -d)"
TMP_RESTORE="$(mktemp -d)"
trap 'rm -rf "$TMP_SRC" "$TMP_OUT" "$TMP_RESTORE"' EXIT

mkdir -p "$TMP_SRC/u1001/data"
echo "backup-check" > "$TMP_SRC/u1001/data/example.txt"

archive="$(bash ops/backup.sh "$TMP_SRC" "$TMP_OUT")"
[[ -f "$archive" ]] || { echo "FAIL: backup archive not created"; exit 1; }

bash ops/restore.sh "$archive" "$TMP_RESTORE"
[[ "$(cat "$TMP_RESTORE/u1001/data/example.txt")" == "backup-check" ]] || { echo "FAIL: restore content mismatch"; exit 1; }

echo "PASS: backup and restore scripts work"
