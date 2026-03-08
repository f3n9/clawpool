#!/usr/bin/env bash
set -euo pipefail

IMAGE_TAG="${1:-openclaw-node-sudo-test}"
BASE_IMAGE="${OPENCLAW_BASE_IMAGE_OVERRIDE:-}"
BUILD_ARGS=()

if [[ -n "$BASE_IMAGE" ]]; then
  BUILD_ARGS+=(--build-arg "OPENCLAW_BASE_IMAGE=$BASE_IMAGE")
fi

docker build -t "$IMAGE_TAG" "${BUILD_ARGS[@]}" infra/docker-build >/dev/null

docker run --rm --entrypoint sh "$IMAGE_TAG" -lc '
  whoami | grep -qx node
  command -v sudo >/dev/null
  sudo -n true
  sudo -n apt-get --version >/dev/null
'
