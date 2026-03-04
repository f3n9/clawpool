#!/usr/bin/env bash
set -euo pipefail

SRC_DIR="${1:-/srv/openclaw/users}"
BACKUP_DIR="${2:-/tmp/openclaw-backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
ARCHIVE="$BACKUP_DIR/openclaw-users-$STAMP.tar.gz"

mkdir -p "$BACKUP_DIR"
tar -czf "$ARCHIVE" -C "$SRC_DIR" .

echo "$ARCHIVE"
