# Keycloak Claim Mapping for OpenClaw

## Goal
Ensure oauth2-proxy forwards a stable user identity header for instance binding.

## Required Mapper (recommended)
In the OpenClaw Keycloak client, create a protocol mapper so `preferred_username` is set to employee id.
- Source: employee attribute (e.g., `employee_id`)
- Target claim: `preferred_username`

## Runtime Flow
- oauth2-proxy reads `preferred_username_claim = preferred_username`
- It emits `X-Auth-Request-User`
- It can also emit `X-Auth-Request-Email` and `X-Auth-Request-Groups` for JIT access control.
- instance-manager uses `X-Employee-Id` first, then `X-Auth-Request-User`, then `X-Forwarded-User`

## Verification
1. Login from browser through oauth2-proxy.
2. Confirm request headers include `X-Auth-Request-User`.
3. If JIT allow-list is enabled, confirm `X-Auth-Request-Email` and/or `X-Auth-Request-Groups` are present.
4. Confirm user routes to `openclaw-<employee_id>`.
