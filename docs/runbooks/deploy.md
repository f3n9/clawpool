# Deployment Runbook

## Bootstrap
1. Provision user directories and default secrets.
2. Fill `infra/.env` from `infra/.env.example`.
3. Start infra stack with compose.

## OpenAI endpoint
- Set `OPENCLAW_DEFAULT_OPENAI_ENDPOINT` in `infra/.env` (default: `https://api.openai.com/v1`).
- This value is seeded into each user's secret space during provisioning.

## Smoke checks
- `bash infra/tests/auth-smoke.sh`
- `bash infra/tests/routing-smoke.sh`
- `bash infra/tests/isolation-check.sh`

## Identity mapping pre-check
- Validate Keycloak claim mapping per `docs/runbooks/keycloak-claims.md`.
