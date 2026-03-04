#!/usr/bin/env bash
set -euo pipefail

COMPOSE="infra/docker-compose.base.yml"
DOC="docs/runbooks/user-config-persistence.md"

[[ -f "$COMPOSE" ]] || { echo "FAIL: missing compose file"; exit 1; }
[[ -f "$DOC" ]] || { echo "FAIL: missing persistence runbook"; exit 1; }

grep -q '/srv/openclaw/users' "$COMPOSE" || { echo "FAIL: missing per-user persistence mount"; exit 1; }
grep -q 'OPENCLAW_CONTAINER_RUNTIME_PATH' "$COMPOSE" || { echo "FAIL: missing runtime mount env"; exit 1; }
grep -q 'Channels' "$DOC" || { echo "FAIL: runbook must mention Channels"; exit 1; }
grep -q 'Skills' "$DOC" || { echo "FAIL: runbook must mention Skills"; exit 1; }
grep -q 'Plugins' "$DOC" || { echo "FAIL: runbook must mention Plugins"; exit 1; }
grep -q '/home/node/.openclaw' "$DOC" || { echo "FAIL: runbook must mention runtime persistence path"; exit 1; }

echo "PASS: persistence policy baseline is documented and mounted"
