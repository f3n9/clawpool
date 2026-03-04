#!/usr/bin/env bash
set -euo pipefail

CFG="infra/oauth2-proxy.cfg"
ENV="infra/.env.example"
CLAIMS_DOC="docs/runbooks/keycloak-claims.md"
COMPOSE="infra/docker-compose.base.yml"

if [[ ! -f "$CFG" ]]; then
  echo "FAIL: missing oauth2 proxy config: $CFG"
  exit 1
fi

if [[ ! -f "$ENV" ]]; then
  echo "FAIL: missing env example: $ENV"
  exit 1
fi
if [[ ! -f "$COMPOSE" ]]; then
  echo "FAIL: missing compose file: $COMPOSE"
  exit 1
fi
if [[ ! -f "$CLAIMS_DOC" ]]; then
  echo "FAIL: missing keycloak claims runbook: $CLAIMS_DOC"
  exit 1
fi

grep -q 'provider = "oidc"' "$CFG" || { echo "FAIL: oidc provider missing"; exit 1; }
grep -q 'pass_user_headers = true' "$CFG" || { echo "FAIL: user headers not enabled"; exit 1; }
grep -q 'user_id_claim' "$CFG" || { echo "FAIL: user id claim not configured"; exit 1; }
grep -q 'preferred_username' "$CLAIMS_DOC" || { echo "FAIL: keycloak mapper guidance missing"; exit 1; }
grep -q 'KEYCLOAK_ISSUER_URL=' "$ENV" || { echo "FAIL: env key missing"; exit 1; }
grep -q 'OPENCLAW_OAUTH2_COOKIE_SECRET=' "$ENV" || { echo "FAIL: oauth2 cookie secret env key missing"; exit 1; }
grep -q 'OAUTH2_PROXY_OIDC_ISSUER_URL' "$COMPOSE" || { echo "FAIL: compose missing oidc issuer env"; exit 1; }
grep -q 'OAUTH2_PROXY_REDIRECT_URL' "$COMPOSE" || { echo "FAIL: compose missing redirect url env"; exit 1; }
grep -q 'OAUTH2_PROXY_COOKIE_SECRET' "$COMPOSE" || { echo "FAIL: compose missing cookie secret env"; exit 1; }

echo "PASS: oauth2-proxy baseline auth config present"
