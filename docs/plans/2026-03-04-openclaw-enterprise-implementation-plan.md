# OpenClaw Enterprise Deployment Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a single-host, per-employee isolated OpenClaw platform with Keycloak SSO, fixed instance mapping, on-demand startup, persistent private data, and per-user API key lifecycle.

**Architecture:** Put Traefik + oauth2-proxy in front of all traffic, authenticate with Keycloak OIDC, resolve user identity to a fixed container via instance-manager, then route to that user container. Each employee has isolated data/config/secrets volumes and resource limits; idle instances auto-stop and low-concurrency windows allow higher per-instance limits.

**Tech Stack:** Docker Engine, Docker Compose, Traefik (or Nginx), oauth2-proxy, Keycloak OIDC, lightweight instance-manager (Go/Node/Python), bash automation scripts, OpenAI API.

---

## Concrete Baseline Config (must be encoded in files)
- Identity mapping:
  - `employee_id` (primary claim), fallback `sub`
  - container name: `openclaw-${employee_id}`
- Per-user storage:
  - `/srv/openclaw/users/<id>/data`
  - `/srv/openclaw/users/<id>/config`
  - `/srv/openclaw/users/<id>/secrets/openai_api_key`
- Permissions:
  - owner: dedicated UID/GID per user container
  - mode: `700` on user directories, `600` on secret files
- Resource baseline:
  - normal: `cpus=0.8`, `mem_limit=1.2g`
  - low-concurrency boost: `cpus=1.5`, `mem_limit=2g`
- Lifecycle:
  - idle-stop threshold: `30m`
  - startup throttling: max `3-5` concurrent starts
  - cold-start SLO: P95 <= `20s`

### Task 1: Create deployment skeleton and required env schema

**Files:**
- Create: `infra/docker-compose.base.yml`
- Create: `infra/.env.example`
- Create: `infra/README.md`

**Step 1: Write failing validation check**
- Run: `docker compose -f infra/docker-compose.base.yml config`
- Expected: FAIL (file not found)

**Step 2: Write minimal compose and env skeleton**
- Add services: `traefik`, `oauth2-proxy`, `instance-manager`, `idle-controller`.
- Add env keys: `KEYCLOAK_ISSUER_URL`, `KEYCLOAK_CLIENT_ID`, `KEYCLOAK_CLIENT_SECRET`, `OPENCLAW_DEFAULT_OPENAI_KEY`, `OPENCLAW_IDLE_MINUTES`, `OPENCLAW_BASE_CPU`, `OPENCLAW_BASE_MEM`, `OPENCLAW_BOOST_CPU`, `OPENCLAW_BOOST_MEM`.

**Step 3: Run config validation**
- Run: `docker compose -f infra/docker-compose.base.yml config`
- Expected: PASS

**Step 4: Commit**
- Run: `git add infra/docker-compose.base.yml infra/.env.example infra/README.md`
- Run: `git commit -m "feat: add infra skeleton and required env schema"`

### Task 2: Implement Keycloak OIDC gateway with stable identity headers

**Files:**
- Modify: `infra/docker-compose.base.yml`
- Create: `infra/oauth2-proxy.cfg`
- Create: `infra/tests/auth-smoke.sh`

**Step 1: Write failing auth smoke test**
- Script checks unauthenticated request redirects to Keycloak and callback emits identity header.

**Step 2: Configure oauth2-proxy**
- Set issuer/client values.
- Forward identity headers (`X-Employee-Id`, fallback `X-User-Sub`).

**Step 3: Run smoke test**
- Run: `bash infra/tests/auth-smoke.sh`
- Expected: redirect + successful callback + identity header present

**Step 4: Commit**
- Run: `git add infra/docker-compose.base.yml infra/oauth2-proxy.cfg infra/tests/auth-smoke.sh`
- Run: `git commit -m "feat: add keycloak oidc auth with identity headers"`

### Task 3: Build fixed-instance resolver

**Files:**
- Create: `services/instance-manager/main.(go|ts|py)`
- Create: `services/instance-manager/mapping.yaml`
- Create: `services/instance-manager/tests/mapping_test.*`

**Step 1: Write failing tests**
- Validate `employee_id -> openclaw-<id>`.
- Validate fallback `sub` and unknown-user rejection behavior.

**Step 2: Implement resolver**
- Rule: prefer deterministic name, optional explicit mapping overrides.

**Step 3: Run tests**
- Run service test command.
- Expected: PASS

**Step 4: Commit**
- Run: `git add services/instance-manager`
- Run: `git commit -m "feat: add fixed user-instance resolver"`

### Task 4: Add on-demand startup, health checks, and startup throttling

**Files:**
- Modify: `services/instance-manager/main.(go|ts|py)`
- Create: `services/instance-manager/tests/startup_test.*`

**Step 1: Write failing tests**
- Start stopped container and wait healthy.
- Enforce max concurrent startups.

**Step 2: Implement Docker control flow**
- `inspect` -> `start` -> poll `health`.
- Queue starts above throttle limit.

**Step 3: Run tests**
- Expected: PASS

**Step 4: Commit**
- Run: `git add services/instance-manager`
- Run: `git commit -m "feat: support on-demand startup with throttling"`

### Task 5: Provision per-user persistent directories, secrets, and ownership

**Files:**
- Create: `infra/scripts/provision-users.sh`
- Create: `infra/scripts/provision-user-secrets.sh`
- Create: `infra/users.csv`
- Create: `infra/tests/isolation-check.sh`

**Step 1: Write failing isolation test**
- Verify user A container cannot read user B `data/config/secrets`.

**Step 2: Implement provisioning scripts**
- Create `/srv/openclaw/users/<id>/{data,config,secrets}`.
- Apply `700` on dirs, `600` on secret file.
- Seed `secrets/openai_api_key` with default key at first provisioning.

**Step 3: Run isolation check**
- Run: `bash infra/tests/isolation-check.sh`
- Expected: PASS

**Step 4: Commit**
- Run: `git add infra/scripts/provision-users.sh infra/scripts/provision-user-secrets.sh infra/users.csv infra/tests/isolation-check.sh`
- Run: `git commit -m "feat: provision isolated user storage and secrets"`

### Task 6: Add reverse-proxy dynamic routing to bound instance

**Files:**
- Create: `infra/traefik/dynamic.yml`
- Modify: `infra/docker-compose.base.yml`
- Create: `infra/tests/routing-smoke.sh`

**Step 1: Write failing routing test**
- Authenticated user routes only to its bound instance.

**Step 2: Implement routing integration**
- Resolve upstream from identity headers via instance-manager.

**Step 3: Run smoke test**
- Run: `bash infra/tests/routing-smoke.sh`
- Expected: PASS

**Step 4: Commit**
- Run: `git add infra/traefik/dynamic.yml infra/docker-compose.base.yml infra/tests/routing-smoke.sh`
- Run: `git commit -m "feat: add per-user dynamic routing"`

### Task 7: Persist user-modified Channels/Skills/Plugins settings

**Files:**
- Modify: `infra/docker-compose.base.yml`
- Create: `infra/tests/persistence-check.sh`
- Create: `docs/runbooks/user-config-persistence.md`

**Step 1: Write failing persistence test**
- Create channel/plugin config via UI/API, restart container, verify settings still exist.

**Step 2: Mount persistent paths**
- Bind app config/plugin dirs to `/srv/openclaw/users/<id>/config` (and relevant plugin data path).

**Step 3: Run persistence check**
- Run: `bash infra/tests/persistence-check.sh`
- Expected: PASS

**Step 4: Commit**
- Run: `git add infra/docker-compose.base.yml infra/tests/persistence-check.sh docs/runbooks/user-config-persistence.md`
- Run: `git commit -m "feat: persist user channels skills and plugins"`

### Task 8: Implement API key lifecycle scripts (default -> per-user key)

**Files:**
- Create: `infra/scripts/rotate-user-openai-key.sh`
- Create: `infra/scripts/audit-default-keys.sh`
- Create: `infra/tests/key-rotation-check.sh`
- Create: `docs/runbooks/key-operations.md`

**Step 1: Write failing key-rotation test**
- Replace one user key and verify only that user instance changes behavior.

**Step 2: Implement scripts**
- `rotate-user-openai-key.sh <employee_id> <new_key>`: update secret file and restart only target container.
- `audit-default-keys.sh`: scan users still using default key and emit non-zero exit if any found.

**Step 3: Run key checks**
- Run: `bash infra/tests/key-rotation-check.sh`
- Run: `bash infra/scripts/audit-default-keys.sh`
- Expected: PASS

**Step 4: Commit**
- Run: `git add infra/scripts/rotate-user-openai-key.sh infra/scripts/audit-default-keys.sh infra/tests/key-rotation-check.sh docs/runbooks/key-operations.md`
- Run: `git commit -m "feat: add per-user openai key lifecycle scripts"`

### Task 9: Add idle-stop and dynamic resource boost controller

**Files:**
- Create: `services/idle-controller/main.(go|ts|py)`
- Create: `services/idle-controller/tests/idle_test.*`
- Create: `services/resource-controller/main.(go|ts|py)`
- Create: `services/resource-controller/tests/resource_policy_test.*`
- Modify: `infra/docker-compose.base.yml`

**Step 1: Write failing tests**
- Stop instances idle > 30m.
- Apply boosted limits when active instances below threshold; revert on higher concurrency.

**Step 2: Implement controllers**
- Idle controller: no active session -> stop container.
- Resource controller: `docker update` on target containers using env-defined baseline/boost values.

**Step 3: Run tests**
- Run controller test commands.
- Expected: PASS

**Step 4: Commit**
- Run: `git add services/idle-controller services/resource-controller infra/docker-compose.base.yml`
- Run: `git commit -m "feat: add idle-stop and dynamic resource policy controllers"`

### Task 10: Add upgrade-safe deployment workflow (data preserved)

**Files:**
- Create: `ops/upgrade-openclaw.sh`
- Create: `ops/rollback-openclaw.sh`
- Create: `infra/tests/upgrade-data-retention-check.sh`
- Create: `docs/runbooks/upgrade.md`

**Step 1: Write failing upgrade retention test**
- Seed sample user data, upgrade image, verify data and config unchanged.

**Step 2: Implement upgrade/rollback scripts**
- Upgrade script replaces image/container only, keeps user volumes unchanged.
- Rollback script restores previous image tag without touching volumes.

**Step 3: Run upgrade test**
- Run: `bash infra/tests/upgrade-data-retention-check.sh`
- Expected: PASS

**Step 4: Commit**
- Run: `git add ops/upgrade-openclaw.sh ops/rollback-openclaw.sh infra/tests/upgrade-data-retention-check.sh docs/runbooks/upgrade.md`
- Run: `git commit -m "ops: add upgrade rollback workflow preserving user data"`

### Task 11: Add backups, restore drills, and operational runbooks

**Files:**
- Create: `ops/backup.sh`
- Create: `ops/restore.sh`
- Create: `docs/runbooks/deploy.md`
- Create: `docs/runbooks/incident.md`

**Step 1: Write failing backup/restore dry-run checks**
- Confirm backup artifacts and restore verification.

**Step 2: Implement scripts and docs**
- Daily incremental user-dir backups; weekly restore drill procedure.

**Step 3: Execute dry runs**
- Run dry-run commands and capture outputs.

**Step 4: Commit**
- Run: `git add ops docs/runbooks`
- Run: `git commit -m "ops: add backup restore scripts and runbooks"`

### Task 12: Load test and acceptance for 40 active users

**Files:**
- Create: `tests/perf/k6-openclaw-sso.js`
- Create: `docs/reports/40-user-validation.md`

**Step 1: Write baseline scenario**
- 40 concurrent authenticated sessions with mixed chat/config actions.

**Step 2: Execute load test**
- Run: `k6 run tests/perf/k6-openclaw-sso.js`

**Step 3: Validate SLO and safety criteria**
- Cold start P95 <= 20s.
- Auth success >= 99%.
- No cross-user data access.
- No OOM during concurrency ramp.

**Step 4: Commit**
- Run: `git add tests/perf docs/reports/40-user-validation.md`
- Run: `git commit -m "test: add 40-user acceptance report"`
