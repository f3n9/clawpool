# Deployment Runbook

## Bootstrap
1. Provision user directories and default secrets.
2. Fill `infra/.env` from `infra/.env.example`.
3. Start infra stack with compose.

## Smoke checks
- `bash infra/tests/auth-smoke.sh`
- `bash infra/tests/routing-smoke.sh`
- `bash infra/tests/isolation-check.sh`
