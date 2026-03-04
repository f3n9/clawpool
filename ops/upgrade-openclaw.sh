#!/usr/bin/env bash
set -euo pipefail

IMAGE_TAG="${1:-latest}"
COMPOSE_FILE="${2:-infra/docker-compose.base.yml}"
STATE_DIR="${STATE_DIR:-/var/lib/openclaw}"
STATE_FILE="$STATE_DIR/deployment.env"
mkdir -p "$STATE_DIR"

current_tag=""
if [[ -f "$STATE_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$STATE_FILE"
  current_tag="${CURRENT_IMAGE_TAG:-}"
fi

echo "Upgrading OpenClaw services to image tag: $IMAGE_TAG"
if command -v docker >/dev/null 2>&1; then
  OPENCLAW_IMAGE_TAG="$IMAGE_TAG" docker compose -f "$COMPOSE_FILE" pull || true
  OPENCLAW_IMAGE_TAG="$IMAGE_TAG" docker compose -f "$COMPOSE_FILE" up -d || true
fi

{
  echo "CURRENT_IMAGE_TAG=$IMAGE_TAG"
  if [[ -n "$current_tag" ]]; then
    echo "PREVIOUS_IMAGE_TAG=$current_tag"
  fi
} > "$STATE_FILE"

echo "Upgrade complete (volumes unchanged)"
