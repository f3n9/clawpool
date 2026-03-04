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
- `OPENCLAW_DEFAULT_OPENAI_KEY`
- `OPENCLAW_DEFAULT_OPENAI_ENDPOINT`
- `OPENCLAW_IDLE_MINUTES`
- `OPENCLAW_BASE_CPU`, `OPENCLAW_BASE_MEM`
- `OPENCLAW_BOOST_CPU`, `OPENCLAW_BOOST_MEM`
- `OPENCLAW_STARTUP_MAX_CONCURRENT`
