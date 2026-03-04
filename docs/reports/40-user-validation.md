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
- `k6 run tests/perf/k6-openclaw-sso.js`
- `bash infra/tests/isolation-check.sh`
- `bash infra/tests/key-rotation-check.sh`

## Result Template
- Date:
- Environment:
- Observed cold start P95:
- Auth success rate:
- OOM observed (yes/no):
- Isolation violations (yes/no):
