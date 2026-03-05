# Infra Bootstrap

This directory contains base deployment assets for enterprise OpenClaw.

## Quick Start

1. Copy `.env.example` to `.env` and fill real values.
   - Keep `traefik/dynamic.yml` router host aligned with `OPENCLAW_HOST`.
2. Verify compose syntax:
   - `docker compose -f infra/docker-compose.base.yml config`
3. Start baseline services:
   - `docker compose -f infra/docker-compose.base.yml up -d`

## Required Environment Keys

- `KEYCLOAK_ISSUER_URL`
- `KEYCLOAK_CLIENT_ID`
- `KEYCLOAK_CLIENT_SECRET`
- `OPENCLAW_OAUTH2_COOKIE_SECRET` (32-char random string)
- `OPENCLAW_JIT_PROVISION` (`true` for first-login auto-provisioning)
- `OPENCLAW_IMAGE`, `OPENCLAW_IMAGE_TAG`
- `OPENCLAW_DOCKER_NETWORK`
- `OPENCLAW_INSTANCE_PORT`
- `OPENCLAW_CONTAINER_DATA_PATH`, `OPENCLAW_CONTAINER_CONFIG_PATH`, `OPENCLAW_CONTAINER_RUNTIME_PATH`
- `OPENCLAW_GATEWAY_AUTH_MODE` (recommended: `trusted-proxy`)
- `OPENCLAW_GATEWAY_TRUSTED_PROXY_USER_HEADER` (recommended: `host` to keep local gateway calls authorized)
- `OPENCLAW_GATEWAY_TRUSTED_PROXIES` (recommended: `127.0.0.1/32,172.16.0.0/12`)
- `OPENCLAW_DEFAULT_CHANNEL_PLUGINS` (recommended: `telegram,googlechat`)
- `OPENCLAW_FORCE_RESPONSES_STORE` (recommended: `true` when using OpenAI-compatible Responses proxy endpoints to avoid multi-turn item-id 400 errors)
- `OPENCLAW_DEFAULT_OPENAI_KEY`
- `OPENCLAW_DEFAULT_OPENAI_ENDPOINT`
- `OPENCLAW_OPENAI_API` (optional override: `openai-responses` or `openai-completions`)
- `OPENCLAW_ALLOWED_MODELS` (must include only approved models, e.g. `gpt-5.2,gpt-5.3-codex,gpt-5.2-chat`)
- `OPENCLAW_DEFAULT_OPENAI_MODEL` (must be one of `OPENCLAW_ALLOWED_MODELS`)
- `OPENCLAW_ALLOWED_EMAIL_DOMAINS`, `OPENCLAW_ALLOWED_GROUPS` (optional access controls for JIT provisioning)
- `OPENCLAW_IDLE_MINUTES`
- `OPENCLAW_BASE_CPU`, `OPENCLAW_BASE_MEM`
- `OPENCLAW_BOOST_CPU`, `OPENCLAW_BOOST_MEM`
- `OPENCLAW_STARTUP_MAX_CONCURRENT`

## Health Check

After login or configuration changes, run:

- `infra/tests/gateway-health-check.sh <user_email> [host]`

Example:

- `infra/tests/gateway-health-check.sh fyue@yinxiang.com claw.hatch.yinxiang.com`

It validates:

- infra services are running (`instance-manager`, `oauth2-proxy`)
- oauth2 session backend (`redis`) is available for stable MFA/OIDC session persistence
- per-user runtime config schema compatibility (`trusted-proxy` auth keys)
- `/resolve` routes to the expected dedicated container
- websocket upgrade returns `101 Switching Protocols`
