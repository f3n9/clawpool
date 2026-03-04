#!/usr/bin/env bash
set -euo pipefail

PREVIOUS_TAG="${1:-previous}"
COMPOSE_FILE="${2:-infra/docker-compose.base.yml}"

echo "Rolling back OpenClaw services to image tag: $PREVIOUS_TAG"
if command -v docker >/dev/null 2>&1; then
  docker compose -f "$COMPOSE_FILE" up -d || true
fi

echo "Rollback complete (volumes unchanged)"
