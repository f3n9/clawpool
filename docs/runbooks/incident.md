# Incident Runbook

## Common incidents
- User cannot log in: verify Keycloak issuer/client and oauth2-proxy.
- User lands on wrong instance: verify identity headers and mapping resolver.
- OOM risk: inspect active instance count and apply base resource profile.

## Immediate actions
- Restart affected user container only.
- Rotate user key if compromise suspected.
- Trigger restore for impacted user's data directory.
