#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash infra/tests/run-40-user-validation.sh [--dry-run]

Env:
  BASE_URL    Target OpenClaw URL (default: http://openclaw.company.internal)
  OUT_DIR     Artifact output directory
  REPORT_PATH Report markdown output path
EOF
}

DRY_RUN=0
if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
BASE_URL="${BASE_URL:-http://openclaw.company.internal}"
K6_SCRIPT="tests/perf/k6-openclaw-sso.js"
OUT_DIR="${OUT_DIR:-docs/reports/artifacts/40-user/$STAMP}"
REPORT_PATH="${REPORT_PATH:-docs/reports/40-user-validation-$STAMP.md}"
mkdir -p "$OUT_DIR"
mkdir -p "$(dirname "$REPORT_PATH")"

K6_OUTPUT="$OUT_DIR/k6-output.txt"
K6_SUMMARY="$OUT_DIR/k6-summary.json"
DOCKER_STATS="$OUT_DIR/docker-stats.txt"
ISOLATION_LOG="$OUT_DIR/isolation-check.txt"

if [[ "$DRY_RUN" -eq 0 ]]; then
  command -v k6 >/dev/null 2>&1 || { echo "k6 is required. Use --dry-run to validate script wiring."; exit 1; }
  [[ -f "$K6_SCRIPT" ]] || { echo "missing k6 script: $K6_SCRIPT"; exit 1; }

  k6 run --env "BASE_URL=$BASE_URL" --summary-export "$K6_SUMMARY" "$K6_SCRIPT" | tee "$K6_OUTPUT"
else
  cat >"$K6_OUTPUT" <<'EOF'
DRY RUN: k6 execution skipped.
EOF
  cat >"$K6_SUMMARY" <<'EOF'
{"metrics":{"http_req_duration":{"values":{"p(95)":12345}},"checks":{"values":{"rate":1}}}}
EOF
fi

if command -v docker >/dev/null 2>&1; then
  if ! docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}' >"$DOCKER_STATS" 2>/dev/null; then
    printf 'docker stats unavailable (permission or daemon issue)\n' >"$DOCKER_STATS"
  fi
else
  printf 'docker command not found\n' >"$DOCKER_STATS"
fi

if bash infra/tests/isolation-check.sh >"$ISOLATION_LOG" 2>&1; then
  ISOLATION="no"
else
  ISOLATION="yes"
fi

read -r P95_MS CHECK_RATE <<<"$(python3 - "$K6_SUMMARY" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

metrics = data.get("metrics", {})
p95 = metrics.get("http_req_duration", {}).get("values", {}).get("p(95)", "n/a")
rate = metrics.get("checks", {}).get("values", {}).get("rate", "n/a")
print(p95, rate)
PY
)"

AUTH_RATE="n/a"
if [[ "$CHECK_RATE" != "n/a" ]]; then
  AUTH_RATE="$(python3 - "$CHECK_RATE" <<'PY'
import sys
rate = float(sys.argv[1])
print(f"{rate * 100:.2f}%")
PY
)"
fi

IMAGE_TAG="${OPENCLAW_IMAGE_TAG:-unknown}"
ENV_DESC="$(uname -srm)"
DATE_STR="$(date '+%Y-%m-%d %H:%M:%S %Z')"
OOM_OBSERVED="unknown"

cat >"$REPORT_PATH" <<EOF
# 40 Active User Validation Report

## Scope
- 40 concurrent authenticated user sessions
- On-demand startup path
- Per-user routing and data isolation safety checks

## SLO Targets
- Cold start P95 <= 20s
- Authentication success >= 99%
- No cross-user data leakage
- No host OOM during ramp

## Commands
- \`k6 run tests/perf/k6-openclaw-sso.js\`
- \`bash infra/tests/isolation-check.sh\`
- \`bash infra/tests/key-rotation-check.sh\`

## Result Template
- Date: $DATE_STR
- Environment: $ENV_DESC
- OpenClaw image tag: $IMAGE_TAG
- Observed cold start P95: ${P95_MS} ms
- Auth success rate: $AUTH_RATE
- OOM observed (yes/no): $OOM_OBSERVED
- Isolation violations (yes/no): $ISOLATION
- Evidence files:
  - k6 summary output: $K6_SUMMARY
  - docker stats snapshot: $DOCKER_STATS
EOF

echo "Generated report: $REPORT_PATH"
