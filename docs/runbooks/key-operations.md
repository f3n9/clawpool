# OpenAI Key Operations

## Initial State
- New users are provisioned with default key only for bootstrap.

## Rotate One User Key
- Run: `bash infra/scripts/rotate-user-openai-key.sh <employee_id> <new_key>`
- The script updates only that user secret and restarts only that user's container.

## Audit Default Key Usage
- Run: `OPENCLAW_DEFAULT_OPENAI_KEY=<default> ROOT_DIR=/srv/openclaw/users bash infra/scripts/audit-default-keys.sh`
- Exit code `0`: all good
- Exit code `2`: some users still use default key
