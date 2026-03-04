# User Config Persistence

This runbook ensures user-modified OpenClaw settings survive container restarts.

## Scope
- Channels configuration
- Skills configuration
- Plugins configuration and plugin state

## Baseline Rule
- Mount user-specific config and plugin paths from `/srv/openclaw/users/<employee_id>/config`.
- Never store user customization only inside container writable layer.

## Validation
1. User updates Channels/Skills/Plugins via WebGUI.
2. Restart target user container.
3. Re-open WebGUI and verify settings unchanged.
