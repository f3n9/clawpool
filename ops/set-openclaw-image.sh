#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: ops/set-openclaw-image.sh --image <image> --tag <tag> [options]

Options:
  --image <image>              Image repository/name (for example: yx-openclaw)
  --tag <tag>                  Image tag (for example: 20260308)
  --env-file <path>            Env file to update (default: infra/.env)
  --compose-file <path>        Compose file for instance-manager refresh (default: infra/docker-compose.base.yml)
  --recreate-existing          Recreate existing openclaw-* containers onto the new image
  -h, --help                   Show this help
USAGE
}

DOCKER_BIN="${DOCKER_BIN:-docker}"
IMAGE=""
TAG=""
ENV_FILE="infra/.env"
COMPOSE_FILE="infra/docker-compose.base.yml"
RECREATE_EXISTING=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image)
      IMAGE="${2:-}"
      shift 2
      ;;
    --tag)
      TAG="${2:-}"
      shift 2
      ;;
    --env-file)
      ENV_FILE="${2:-}"
      shift 2
      ;;
    --compose-file)
      COMPOSE_FILE="${2:-}"
      shift 2
      ;;
    --recreate-existing)
      RECREATE_EXISTING=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

[[ -n "$IMAGE" ]] || { echo "--image is required" >&2; exit 1; }
[[ -n "$TAG" ]] || { echo "--tag is required" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "Env file not found: $ENV_FILE" >&2; exit 1; }
[[ -f "$COMPOSE_FILE" ]] || { echo "Compose file not found: $COMPOSE_FILE" >&2; exit 1; }

FULL_IMAGE="$IMAGE:$TAG"

update_env_file() {
  python3 - "$ENV_FILE" "$IMAGE" "$TAG" <<'PY'
from pathlib import Path
import sys

env_path = Path(sys.argv[1])
image = sys.argv[2]
tag = sys.argv[3]
lines = env_path.read_text(encoding='utf-8').splitlines()
replacements = {
    'OPENCLAW_IMAGE': image,
    'OPENCLAW_IMAGE_TAG': tag,
}
seen = set()
out = []
for line in lines:
    raw = line.rstrip('\n')
    if '=' not in raw or raw.lstrip().startswith('#'):
        out.append(raw)
        continue
    key, _, _value = raw.partition('=')
    if key in replacements:
        out.append(f'{key}={replacements[key]}')
        seen.add(key)
    else:
        out.append(raw)
for key in ('OPENCLAW_IMAGE', 'OPENCLAW_IMAGE_TAG'):
    if key not in seen:
        out.append(f'{key}={replacements[key]}')
env_path.write_text('\n'.join(out) + '\n', encoding='utf-8')
PY
}

list_openclaw_containers() {
  "$DOCKER_BIN" ps -a --format '{{.Names}}' | grep '^openclaw-' || true
}

container_exists() {
  local name="$1"
  list_openclaw_containers | grep -Fxq "$name"
}

create_args_from_inspect() {
  local inspect_file="$1"
  python3 - "$inspect_file" "$FULL_IMAGE" <<'PY'
import json
import sys
from pathlib import Path

inspect_path = Path(sys.argv[1])
full_image = sys.argv[2]
payload = json.loads(inspect_path.read_text(encoding='utf-8'))[0]
config = payload.get('Config') or {}
host = payload.get('HostConfig') or {}
labels = config.get('Labels') or {}
args = []
name = (payload.get('Name') or '').lstrip('/')
if name:
    args.extend(['--name', name])
network_mode = host.get('NetworkMode')
if isinstance(network_mode, str) and network_mode:
    args.extend(['--network', network_mode])
restart_name = ((host.get('RestartPolicy') or {}).get('Name') or '').strip()
if restart_name:
    args.extend(['--restart', restart_name])
nano_cpus = int(host.get('NanoCpus') or 0)
if nano_cpus > 0:
    args.extend(['--cpus', f'{nano_cpus / 1_000_000_000:g}'])
memory = int(host.get('Memory') or 0)
if memory > 0:
    args.extend(['--memory', str(memory)])
working_dir = (config.get('WorkingDir') or '').strip()
if working_dir:
    args.extend(['-w', working_dir])
user = (config.get('User') or '').strip()
if user:
    args.extend(['-u', user])
for bind in host.get('Binds') or []:
    args.extend(['-v', bind])
for env in config.get('Env') or []:
    args.extend(['-e', env])
for key in sorted(labels):
    if key.startswith('org.opencontainers.image.'):
        continue
    args.extend(['--label', f'{key}={labels[key]}'])
for port in sorted((config.get('ExposedPorts') or {}).keys()):
    args.extend(['--expose', port])
entrypoint = config.get('Entrypoint') or []
if isinstance(entrypoint, list) and entrypoint:
    args.extend(['--entrypoint', entrypoint[0]])
args.append(full_image)
cmd = config.get('Cmd') or []
if isinstance(cmd, list):
    args.extend(cmd)
for arg in args:
    sys.stdout.buffer.write(arg.encode('utf-8'))
    sys.stdout.buffer.write(b'\0')
PY
}

restore_previous_container() {
  local name="$1"
  local backup_name="$2"
  local was_running="$3"
  "$DOCKER_BIN" rename "$backup_name" "$name"
  if [[ "$was_running" == "true" ]]; then
    "$DOCKER_BIN" start "$name" >/dev/null
  fi
}

recreate_container() {
  local name="$1"
  local inspect_file backup_name was_running
  inspect_file="$(mktemp)"
  "$DOCKER_BIN" inspect "$name" > "$inspect_file"
  backup_name="${name}.previous"
  if container_exists "$backup_name"; then
    echo "Backup container already exists: $backup_name" >&2
    rm -f "$inspect_file"
    exit 1
  fi
  was_running="$(python3 - "$inspect_file" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], 'r', encoding='utf-8'))[0]
print('true' if payload.get('State', {}).get('Running') else 'false')
PY
)"

  mapfile -d '' -t create_args < <(create_args_from_inspect "$inspect_file")

  if [[ "$was_running" == "true" ]]; then
    "$DOCKER_BIN" stop "$name" >/dev/null
  fi
  "$DOCKER_BIN" rename "$name" "$backup_name"

  if ! "$DOCKER_BIN" create "${create_args[@]}" >/dev/null; then
    restore_previous_container "$name" "$backup_name" "$was_running"
    rm -f "$inspect_file"
    echo "Failed to create replacement container for $name" >&2
    exit 1
  fi

  if [[ "$was_running" == "true" ]]; then
    if ! "$DOCKER_BIN" start "$name" >/dev/null; then
      "$DOCKER_BIN" rm "$name" >/dev/null 2>&1 || true
      restore_previous_container "$name" "$backup_name" "$was_running"
      rm -f "$inspect_file"
      echo "Failed to start replacement container for $name" >&2
      exit 1
    fi
  fi

  "$DOCKER_BIN" rm "$backup_name" >/dev/null
  rm -f "$inspect_file"
}

echo "Updating default OpenClaw image to $FULL_IMAGE"
"$DOCKER_BIN" image inspect "$FULL_IMAGE" >/dev/null
update_env_file
"$DOCKER_BIN" compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --no-deps instance-manager >/dev/null

echo "instance-manager refreshed with OPENCLAW_IMAGE=$IMAGE and OPENCLAW_IMAGE_TAG=$TAG"

mapfile -t OPENCLAW_CONTAINERS < <(list_openclaw_containers)
if [[ "$RECREATE_EXISTING" -ne 1 ]]; then
  if [[ ${#OPENCLAW_CONTAINERS[@]} -gt 0 ]]; then
    printf 'Existing containers left unchanged:'
    for container in "${OPENCLAW_CONTAINERS[@]}"; do
      printf ' %s' "$container"
    done
    printf '\n'
  fi
  exit 0
fi

for container in "${OPENCLAW_CONTAINERS[@]}"; do
  echo "Recreating $container onto $FULL_IMAGE"
  recreate_container "$container"
done

echo "Updated ${#OPENCLAW_CONTAINERS[@]} openclaw container(s) to $FULL_IMAGE"
