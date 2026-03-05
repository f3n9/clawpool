#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./infra/scripts/cleanup-user-data.sh [options] <user_id>

Description:
  Remove one user's OpenClaw container(s) and local persisted data so the user
  returns to a pre-first-login state.

Options:
  -n, --dry-run           Show what would be deleted, but do not delete.
  -y, --yes               Skip interactive confirmation.
      --sudo              Use sudo for Docker and file deletion commands.
      --root-dir <path>   Override OPENCLAW users root directory.
  -h, --help              Show this help.

Examples:
  ./infra/scripts/cleanup-user-data.sh fyue@brainiac.so
  ./infra/scripts/cleanup-user-data.sh --dry-run fyue@brainiac.so
  ./infra/scripts/cleanup-user-data.sh --sudo fyue@brainiac.so
  ./infra/scripts/cleanup-user-data.sh --yes --root-dir /srv/openclaw/users fyue@brainiac.so
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

DRY_RUN=0
ASSUME_YES=0
USE_SUDO=0
ROOT_DIR="${ROOT_DIR:-}"
USER_ID=""

read_env_users_root() {
  local env_file="$REPO_ROOT/infra/.env"
  [[ -f "$env_file" ]] || return 1
  local raw
  raw="$(awk -F= '/^OPENCLAW_USERS_ROOT=/{print $2; exit}' "$env_file" | tr -d '\r' || true)"
  raw="${raw%\"}"
  raw="${raw#\"}"
  raw="${raw%\'}"
  raw="${raw#\'}"
  [[ -n "$raw" ]] || return 1
  printf '%s\n' "$raw"
  return 0
}

normalize_identity() {
  local input="$1"
  local safe
  safe="$(printf '%s' "$input" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9._-]+/-/g; s/-{2,}/-/g; s/^[.-]+//; s/[.-]+$//')"
  safe="${safe:0:96}"
  printf '%s\n' "$safe"
}

run_cmd() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '[dry-run] %q' "$1"
    shift || true
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
    return 0
  fi
  "$@"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--dry-run)
      DRY_RUN=1
      shift
      ;;
    -y|--yes)
      ASSUME_YES=1
      shift
      ;;
    --sudo)
      USE_SUDO=1
      shift
      ;;
    --root-dir)
      [[ $# -ge 2 ]] || { echo "missing value for --root-dir" >&2; exit 1; }
      ROOT_DIR="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      echo "unknown option: $1" >&2
      usage
      exit 1
      ;;
    *)
      if [[ -n "$USER_ID" ]]; then
        echo "unexpected extra argument: $1" >&2
        usage
        exit 1
      fi
      USER_ID="$1"
      shift
      ;;
  esac
done

[[ -n "$USER_ID" ]] || { usage; exit 1; }

if [[ "$USE_SUDO" -eq 1 ]] && ! command -v sudo >/dev/null 2>&1; then
  echo "--sudo was set but sudo is not available on this machine." >&2
  exit 1
fi

if [[ -z "$ROOT_DIR" ]]; then
  if ! ROOT_DIR="$(read_env_users_root)"; then
    ROOT_DIR="/srv/openclaw/users"
  fi
fi

NORMALIZED_ID="$(normalize_identity "$USER_ID")"
if [[ -z "$NORMALIZED_ID" ]]; then
  echo "invalid user id: $USER_ID" >&2
  exit 1
fi

TARGET_DIRS=("$ROOT_DIR/$NORMALIZED_ID")
if [[ "$USER_ID" != "$NORMALIZED_ID" ]]; then
  TARGET_DIRS+=("$ROOT_DIR/$USER_ID")
fi

declare -A CONTAINER_MAP=()
DOCKER_AVAILABLE=0
DOCKER_CMD=(docker)
if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    DOCKER_AVAILABLE=1
    DOCKER_CMD=(docker)
  elif [[ "$USE_SUDO" -eq 1 ]] && sudo -n docker info >/dev/null 2>&1; then
    DOCKER_AVAILABLE=1
    DOCKER_CMD=(sudo docker)
  else
    if [[ "$USE_SUDO" -eq 1 ]]; then
      echo "Note: unable to access Docker API even with --sudo check. Container cleanup will be skipped."
    else
      echo "Note: docker is installed but current user cannot access Docker API. Re-run with --sudo to remove containers."
    fi
  fi
fi

if [[ "$DOCKER_AVAILABLE" -eq 1 ]]; then
  while read -r cid cname; do
    [[ -n "${cname:-}" ]] || continue
    CONTAINER_MAP["$cname"]="$cid"
  done < <("${DOCKER_CMD[@]}" ps -a --filter "name=^/openclaw-${NORMALIZED_ID}$" --format '{{.ID}} {{.Names}}')

  while read -r cid cname; do
    [[ -n "${cname:-}" ]] || continue
    CONTAINER_MAP["$cname"]="$cid"
  done < <("${DOCKER_CMD[@]}" ps -a --filter "label=openclaw.identity=${NORMALIZED_ID}" --format '{{.ID}} {{.Names}}')
fi

echo "Cleanup plan"
echo "  user_id:        $USER_ID"
echo "  normalized_id:  $NORMALIZED_ID"
echo "  users_root:     $ROOT_DIR"
echo ""

echo "Containers to remove:"
if [[ "${#CONTAINER_MAP[@]}" -eq 0 ]]; then
  echo "  (none)"
else
  for name in "${!CONTAINER_MAP[@]}"; do
    echo "  - $name (${CONTAINER_MAP[$name]})"
  done
fi

echo "Directories to remove:"
for path in "${TARGET_DIRS[@]}"; do
  if [[ -e "$path" ]]; then
    echo "  - $path"
  else
    echo "  - $path (not found)"
  fi
done

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo ""
  echo "Dry-run only. No changes made."
  exit 0
fi

if [[ "$ASSUME_YES" -ne 1 ]]; then
  echo ""
  read -r -p "Type YES to continue: " confirm
  [[ "$confirm" == "YES" ]] || { echo "Cancelled."; exit 1; }
fi

for name in "${!CONTAINER_MAP[@]}"; do
  run_cmd "${DOCKER_CMD[@]}" stop "$name" >/dev/null 2>&1 || true
  run_cmd "${DOCKER_CMD[@]}" rm "$name" >/dev/null 2>&1 || true
done

DELETE_FAILED=0
for path in "${TARGET_DIRS[@]}"; do
  [[ -e "$path" ]] || continue
  if [[ "$path" == "/" || "$path" == "$ROOT_DIR" || -z "$path" ]]; then
    echo "refusing unsafe delete target: $path" >&2
    exit 1
  fi
  if [[ "$USE_SUDO" -eq 1 ]]; then
    run_cmd sudo rm -rf "$path"
  else
    if ! run_cmd rm -rf "$path"; then
      echo "Failed to remove $path (permission denied). Re-run with --sudo." >&2
      DELETE_FAILED=1
    fi
  fi
done

if [[ "$DELETE_FAILED" -ne 0 ]]; then
  exit 1
fi

echo ""
echo "Done. User reset complete for: $USER_ID"
