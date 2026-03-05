# Deployment Runbook

## Bootstrap
1. Fill `infra/.env` from `infra/.env.example`.
   - `OPENCLAW_HOST` must be host only (no `https://`), e.g. `claw.hatch.yinxiang.com`.
   - Generate `OPENCLAW_OAUTH2_COOKIE_SECRET` as a 32-char random string, e.g.:
     - `tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32`
   - Keep `infra/traefik/dynamic.yml` router host aligned with `OPENCLAW_HOST`.
2. If `OPENCLAW_JIT_PROVISION=false`, pre-provision user directories and default secrets.
   - `bash infra/scripts/provision-users.sh infra/users.csv`
   - `bash infra/scripts/provision-user-secrets.sh infra/users.csv`
3. Start infra stack with compose.

If `OPENCLAW_JIT_PROVISION=true`, users and containers are created on first successful login.
Use `OPENCLAW_ALLOWED_EMAIL_DOMAINS` and/or `OPENCLAW_ALLOWED_GROUPS` to restrict who can auto-provision.

## OpenAI endpoint
- Set `OPENCLAW_DEFAULT_OPENAI_ENDPOINT` in `infra/.env` (default: `https://api.openai.com/v1`).
- This value is seeded into each user's secret space during provisioning.

## OpenAI model policy
- Set `OPENCLAW_ALLOWED_MODELS` to approved models only (recommended: `gpt-5.2,gpt-5.3-codex,gpt-5.3-chat`).
- Set `OPENCLAW_DEFAULT_OPENAI_MODEL` and ensure it is included in `OPENCLAW_ALLOWED_MODELS`.
- Provisioning fails fast when default model is outside the whitelist.

## Smoke checks
- `bash infra/tests/auth-smoke.sh`
- `bash infra/tests/routing-smoke.sh`
- `bash infra/tests/isolation-check.sh`
- `bash infra/tests/model-policy-check.sh`
- `bash infra/tests/perf-40-baseline-check.sh`

## 40-user validation run
- Execute load + collect evidence + render report:
  - `BASE_URL=http://openclaw.company.internal bash infra/tests/run-40-user-validation.sh`
- Dry run (without k6, for pipeline/script sanity):
  - `bash infra/tests/run-40-user-validation.sh --dry-run`
- Make shortcuts:
  - `make perf-40`
  - `make perf-40-dry`

## Identity mapping pre-check
- Validate Keycloak claim mapping per `docs/runbooks/keycloak-claims.md`.
- Persistence paths and full-rebuild deletion checklist: `docs/runbooks/user-config-persistence.md`.
- Note: user runtime state is persisted under `/srv/openclaw/users/<normalized_identity>/runtime` (mounted to `/home/node/.openclaw`).

## Identity audit logs
- Follow instance-manager identity audit stream:
  - `docker logs -f infra-instance-manager-1 2>&1 | grep 'identity_routed\\|identity_denied'`
- Lifecycle meanings in `identity_routed`:
  - `new`: first login, instance created
  - `running`: user logged in while instance already running
  - `restart`: user logged in and stopped instance was started again
