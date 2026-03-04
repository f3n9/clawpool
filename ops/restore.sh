#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${1:-}"
DEST_DIR="${2:-/tmp/openclaw-restore}"

[[ -n "$ARCHIVE" ]] || { echo "Usage: $0 <archive> [dest_dir]"; exit 1; }
[[ -f "$ARCHIVE" ]] || { echo "Archive not found: $ARCHIVE"; exit 1; }

mkdir -p "$DEST_DIR"
tar -xzf "$ARCHIVE" -C "$DEST_DIR"

echo "Restored to $DEST_DIR"
