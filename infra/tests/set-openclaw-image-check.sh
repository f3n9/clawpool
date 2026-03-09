#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)/.."
SCRIPT="$ROOT_DIR/ops/set-openclaw-image.sh"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

FAKE_DOCKER="$TMPDIR/docker"
DOCKER_LOG="$TMPDIR/docker.log"
export DOCKER_LOG

cat > "$FAKE_DOCKER" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$DOCKER_LOG"
if [[ "$1" == "image" && "$2" == "inspect" && "$3" == "yx-openclaw:20260308" ]]; then
  exit 0
fi
if [[ "$1" == "compose" ]]; then
  exit 0
fi
if [[ "$1" == "ps" && "$2" == "-a" ]]; then
  printf 'openclaw-u1001\ninfra-instance-manager-1\nopenclaw-u1002\n'
  exit 0
fi
if [[ "$1" == "inspect" && "$2" == "openclaw-u1001" ]]; then
  cat <<'JSON'
[
  {
    "Name": "/openclaw-u1001",
    "State": {"Running": true},
    "Config": {
      "Env": ["OPENAI_API_KEY=k1", "OPENCLAW_GATEWAY_TOKEN=t1"],
      "Cmd": ["sh", "-lc", "node -e 'a\nSPLIT_SENTINEL\nb' || true; exec node openclaw.mjs gateway --allow-unconfigured"],
      "WorkingDir": "/app",
      "ExposedPorts": {"18789/tcp": {}},
      "Labels": {"openclaw.managed": "true", "openclaw.identity": "u1001", "org.opencontainers.image.version": "2026.3.2"}
    },
    "HostConfig": {
      "Binds": ["/srv/openclaw/users/u1001/data:/app/data", "/srv/openclaw/users/u1001/runtime:/home/node/.openclaw"],
      "NetworkMode": "infra_default",
      "RestartPolicy": {"Name": "unless-stopped"},
      "NanoCpus": 800000000,
      "Memory": 1288490188
    }
  }
]
JSON
  exit 0
fi
if [[ "$1" == "inspect" && "$2" == "openclaw-u1002" ]]; then
  cat <<'JSON'
[
  {
    "Name": "/openclaw-u1002",
    "State": {"Running": false},
    "Config": {
      "Env": ["OPENAI_API_KEY=k2"],
      "Cmd": ["sh", "-lc", "exec node openclaw.mjs gateway --allow-unconfigured"],
      "WorkingDir": "/app",
      "ExposedPorts": {"18789/tcp": {}},
      "Labels": {"openclaw.managed": "true", "openclaw.identity": "u1002"}
    },
    "HostConfig": {
      "Binds": ["/srv/openclaw/users/u1002/data:/app/data"],
      "NetworkMode": "infra_default",
      "RestartPolicy": {"Name": "unless-stopped"},
      "NanoCpus": 0,
      "Memory": 0
    }
  }
]
JSON
  exit 0
fi
if [[ "$1" == "create" ]]; then
  printf 'create-argc=%s\n' "$#" >> "$DOCKER_LOG"
  idx=0
  for arg in "$@"; do
    printf 'create-arg-%03d=%q\n' "$idx" "$arg" >> "$DOCKER_LOG"
    idx=$((idx + 1))
  done
  exit 0
fi
if [[ "$1" == "stop" || "$1" == "rename" || "$1" == "rm" || "$1" == "start" ]]; then
  exit 0
fi
printf 'unexpected docker invocation: %s\n' "$*" >&2
exit 1
SH
chmod 0755 "$FAKE_DOCKER"

assert_contains() {
  local needle="$1"
  local haystack="$2"
  if ! grep -Fq -- "$needle" "$haystack"; then
    echo "FAIL: expected to find '$needle' in $haystack" >&2
    exit 1
  fi
}

assert_not_contains() {
  local needle="$1"
  local haystack="$2"
  if grep -Fq -- "$needle" "$haystack"; then
    echo "FAIL: did not expect to find '$needle' in $haystack" >&2
    exit 1
  fi
}

run_defaults_only() {
  : > "$DOCKER_LOG"
  local env_file="$TMPDIR/defaults.env"
  cat > "$env_file" <<'ENV'
OPENCLAW_IMAGE=yx-openclaw
OPENCLAW_IMAGE_TAG=20260307
OPENCLAW_HOST=claw.example.com
ENV

  PATH="$TMPDIR:$PATH" DOCKER_BIN="$FAKE_DOCKER" bash "$SCRIPT" \
    --image yx-openclaw \
    --tag 20260308 \
    --env-file "$env_file" \
    --compose-file infra/docker-compose.base.yml

  assert_contains 'OPENCLAW_IMAGE=yx-openclaw' "$env_file"
  assert_contains 'OPENCLAW_IMAGE_TAG=20260308' "$env_file"
  assert_contains "compose --env-file $env_file -f infra/docker-compose.base.yml up -d --no-deps instance-manager" "$DOCKER_LOG"
  assert_not_contains 'create --name openclaw-u1001' "$DOCKER_LOG"
}

run_recreate() {
  : > "$DOCKER_LOG"
  local env_file="$TMPDIR/recreate.env"
  cat > "$env_file" <<'ENV'
OPENCLAW_IMAGE=yx-openclaw
OPENCLAW_IMAGE_TAG=20260307
ENV

  PATH="$TMPDIR:$PATH" DOCKER_BIN="$FAKE_DOCKER" bash "$SCRIPT" \
    --image yx-openclaw \
    --tag 20260308 \
    --env-file "$env_file" \
    --compose-file infra/docker-compose.base.yml \
    --recreate-existing

  assert_contains 'OPENCLAW_IMAGE_TAG=20260308' "$env_file"
  assert_contains 'image inspect yx-openclaw:20260308' "$DOCKER_LOG"
  assert_contains 'ps -a --format {{.Names}}' "$DOCKER_LOG"
  assert_contains 'stop openclaw-u1001' "$DOCKER_LOG"
  assert_contains 'rename openclaw-u1001 openclaw-u1001.previous' "$DOCKER_LOG"
  assert_contains 'create --name openclaw-u1001 --network infra_default --restart unless-stopped --cpus 0.8 --memory 1288490188' "$DOCKER_LOG"
  assert_contains 'create --name openclaw-u1002 --network infra_default --restart unless-stopped' "$DOCKER_LOG"
  assert_contains '--label openclaw.managed=true' "$DOCKER_LOG"
  assert_contains '--label openclaw.identity=u1001' "$DOCKER_LOG"
  assert_contains '-e OPENAI_API_KEY=k1' "$DOCKER_LOG"
  assert_contains 'yx-openclaw:20260308 sh -lc exec node openclaw.mjs gateway --allow-unconfigured' "$DOCKER_LOG"
  assert_contains "a\\nSPLIT_SENTINEL\\nb" "$DOCKER_LOG"
  assert_contains 'start openclaw-u1001' "$DOCKER_LOG"
  assert_not_contains 'start openclaw-u1002' "$DOCKER_LOG"
  assert_contains 'rm openclaw-u1001.previous' "$DOCKER_LOG"
  assert_contains 'rm openclaw-u1002.previous' "$DOCKER_LOG"
}

run_defaults_only
run_recreate

echo 'PASS: set-openclaw-image script checks passed'
