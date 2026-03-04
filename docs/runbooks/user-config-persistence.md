# User Config Persistence

This runbook ensures user-modified OpenClaw settings survive container restarts.

## Scope
- Channels configuration
- Skills configuration
- Plugins configuration and plugin state
- Runtime metadata under `/home/node/.openclaw` (gateway token, local state, logs, jobs)

## Baseline Rule
- Mount user-specific config and plugin paths from `/srv/openclaw/users/<employee_id>/config`.
- Mount OpenClaw runtime path from `/srv/openclaw/users/<employee_id>/runtime` -> `/home/node/.openclaw`.
- Never store user customization only inside container writable layer.

## Validation
1. User updates Channels/Skills/Plugins via WebGUI.
2. Restart target user container.
3. Re-open WebGUI and verify settings unchanged.

## Full Rebuild (Single User)
Use this only when you want a truly clean user instance (all local history/config removed).

1. Stop and remove user container:
   - `docker rm -f openclaw-<normalized_identity>`
2. Delete user persisted data paths:
   - `/srv/openclaw/users/<normalized_identity>/data`
   - `/srv/openclaw/users/<normalized_identity>/config`
   - `/srv/openclaw/users/<normalized_identity>/runtime`
   - `/srv/openclaw/users/<normalized_identity>/secrets` (only if you also want to reset API key/model/endpoint)
3. Next login will provision a new clean instance.
