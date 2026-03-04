# Infra Bootstrap

This directory contains base deployment assets for enterprise OpenClaw.

## Quick Start

1. Copy `.env.example` to `.env` and fill real values.
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
- `OPENCLAW_DEFAULT_OPENAI_KEY`
- `OPENCLAW_DEFAULT_OPENAI_ENDPOINT`
- `OPENCLAW_ALLOWED_MODELS` (must include only approved models, e.g. `gpt-5.2,gpt-5.3-codex`)
- `OPENCLAW_DEFAULT_OPENAI_MODEL` (must be one of `OPENCLAW_ALLOWED_MODELS`)
- `OPENCLAW_ALLOWED_EMAIL_DOMAINS`, `OPENCLAW_ALLOWED_GROUPS` (optional access controls for JIT provisioning)
- `OPENCLAW_IDLE_MINUTES`
- `OPENCLAW_BASE_CPU`, `OPENCLAW_BASE_MEM`
- `OPENCLAW_BOOST_CPU`, `OPENCLAW_BOOST_MEM`
- `OPENCLAW_STARTUP_MAX_CONCURRENT`
