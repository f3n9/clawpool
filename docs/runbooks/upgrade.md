# Upgrade and Rollback

## Upgrade Principles
- Replace image/container only.
- Keep all user volumes untouched.
- Validate user data and config after rollout.

## Upgrade
- `bash ops/upgrade-openclaw.sh <new_tag> infra/docker-compose.base.yml`

## Rollback
- `bash ops/rollback-openclaw.sh <old_tag> infra/docker-compose.base.yml`

## Post-check
- Run data retention check script.
