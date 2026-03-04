#!/usr/bin/env bash
set -euo pipefail

PERF_SCRIPT="tests/perf/k6-openclaw-sso.js"
REPORT_TEMPLATE="docs/reports/40-user-validation.md"
RUNNER_SCRIPT="infra/tests/run-40-user-validation.sh"

[[ -f "$PERF_SCRIPT" ]] || { echo "FAIL: missing perf script"; exit 1; }
[[ -f "$REPORT_TEMPLATE" ]] || { echo "FAIL: missing 40-user report template"; exit 1; }
[[ -f "$RUNNER_SCRIPT" ]] || { echo "FAIL: missing 40-user runner script"; exit 1; }

grep -q 'vus: 40' "$PERF_SCRIPT" || { echo "FAIL: perf script must set vus=40"; exit 1; }
grep -q "p(95)<20000" "$PERF_SCRIPT" || { echo "FAIL: perf script must set p95 threshold <=20s"; exit 1; }
grep -q "checks: \\['rate>0.99'\\]" "$PERF_SCRIPT" || { echo "FAIL: perf script must set success threshold >=99%"; exit 1; }
grep -q 'k6 run --env "BASE_URL=' "$RUNNER_SCRIPT" || { echo "FAIL: runner must pass BASE_URL to k6"; exit 1; }
grep -q 'Generated report:' "$RUNNER_SCRIPT" || { echo "FAIL: runner must emit report path"; exit 1; }

grep -q '^## Scope' "$REPORT_TEMPLATE" || { echo "FAIL: report missing Scope section"; exit 1; }
grep -q '^## SLO Targets' "$REPORT_TEMPLATE" || { echo "FAIL: report missing SLO section"; exit 1; }
grep -q '^## Result Template' "$REPORT_TEMPLATE" || { echo "FAIL: report missing Result Template section"; exit 1; }
grep -q 'Observed cold start P95' "$REPORT_TEMPLATE" || { echo "FAIL: report missing cold start field"; exit 1; }

echo "PASS: 40-user performance baseline assets are complete"
