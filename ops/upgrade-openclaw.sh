#!/usr/bin/env bash
set -euo pipefail

IMAGE_TAG="${1:-latest}"
COMPOSE_FILE="${2:-infra/docker-compose.base.yml}"

echo "Upgrading OpenClaw services to image tag: $IMAGE_TAG"
if command -v docker >/dev/null 2>&1; then
  docker compose -f "$COMPOSE_FILE" pull || true
  docker compose -f "$COMPOSE_FILE" up -d || true
fi

echo "Upgrade complete (volumes unchanged)"
