# Keycloak Claim Mapping for OpenClaw

## Goal
Ensure oauth2-proxy forwards a stable user identity header for instance binding.

## Required Mapper (recommended)
In the OpenClaw Keycloak client, create a protocol mapper so `preferred_username` is set to employee id.
- Source: employee attribute (e.g., `employee_id`)
- Target claim: `preferred_username`

## Runtime Flow
- oauth2-proxy reads `user_id_claim = preferred_username`
- It emits `X-Auth-Request-User` and can emit `X-Auth-Request-Email` / `X-Auth-Request-Groups`.
- instance-manager identity header priority is:
  - `X-Employee-Id`
  - `X-Auth-Request-Email`
  - `X-Forwarded-Email`
  - `X-Auth-Request-User`
  - `X-Forwarded-User`
- `/resolve?employee_id=...` query override is accepted only for loopback requests without auth headers (internal diagnostics).

## Verification
1. Login from browser through oauth2-proxy.
2. Confirm request headers include `X-Auth-Request-User` and (recommended) `X-Auth-Request-Email`.
3. If JIT allow-list is enabled, confirm `X-Auth-Request-Email` and/or `X-Auth-Request-Groups` are present.
4. Confirm user routes to `openclaw-<employee_id>`.
