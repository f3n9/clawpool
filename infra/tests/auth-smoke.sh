#!/usr/bin/env bash
set -euo pipefail

CFG="infra/oauth2-proxy.cfg"
ENV="infra/.env.example"
CLAIMS_DOC="docs/runbooks/keycloak-claims.md"

if [[ ! -f "$CFG" ]]; then
  echo "FAIL: missing oauth2 proxy config: $CFG"
  exit 1
fi

if [[ ! -f "$ENV" ]]; then
  echo "FAIL: missing env example: $ENV"
  exit 1
fi
if [[ ! -f "$CLAIMS_DOC" ]]; then
  echo "FAIL: missing keycloak claims runbook: $CLAIMS_DOC"
  exit 1
fi

grep -q 'provider = "oidc"' "$CFG" || { echo "FAIL: oidc provider missing"; exit 1; }
grep -q 'oidc_issuer_url' "$CFG" || { echo "FAIL: issuer missing"; exit 1; }
grep -q 'set_xauthrequest = true' "$CFG" || { echo "FAIL: xauthrequest not enabled"; exit 1; }
grep -q 'pass_user_headers = true' "$CFG" || { echo "FAIL: user headers not enabled"; exit 1; }
grep -q 'preferred_username_claim' "$CFG" || { echo "FAIL: preferred username claim not configured"; exit 1; }
grep -q 'preferred_username' "$CLAIMS_DOC" || { echo "FAIL: keycloak mapper guidance missing"; exit 1; }
grep -q 'KEYCLOAK_ISSUER_URL=' "$ENV" || { echo "FAIL: env key missing"; exit 1; }

echo "PASS: oauth2-proxy baseline auth config present"
