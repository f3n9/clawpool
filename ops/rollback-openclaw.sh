#!/usr/bin/env bash
set -euo pipefail

PREVIOUS_TAG="${1:-}"
COMPOSE_FILE="${2:-infra/docker-compose.base.yml}"
STATE_DIR="${STATE_DIR:-/var/lib/openclaw}"
STATE_FILE="$STATE_DIR/deployment.env"

if [[ -z "$PREVIOUS_TAG" && -f "$STATE_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$STATE_FILE"
  PREVIOUS_TAG="${PREVIOUS_IMAGE_TAG:-}"
fi

[[ -n "$PREVIOUS_TAG" ]] || { echo "No rollback tag provided or recorded"; exit 1; }

echo "Rolling back OpenClaw services to image tag: $PREVIOUS_TAG"
if command -v docker >/dev/null 2>&1; then
  OPENCLAW_IMAGE_TAG="$PREVIOUS_TAG" docker compose -f "$COMPOSE_FILE" up -d || true
fi

if [[ -f "$STATE_FILE" ]]; then
  {
    echo "CURRENT_IMAGE_TAG=$PREVIOUS_TAG"
    echo "PREVIOUS_IMAGE_TAG="
  } > "$STATE_FILE"
fi

echo "Rollback complete (volumes unchanged)"
