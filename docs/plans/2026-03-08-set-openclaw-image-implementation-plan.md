# Set OpenClaw Image Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an operator script that updates the default OpenClaw runtime image/tag, refreshes the running `instance-manager` service to use those defaults, and optionally recreates existing `openclaw-*` containers onto the new image while preserving their container settings and running/stopped state.

**Architecture:** Add a dedicated ops script instead of overloading the existing upgrade helper. The script rewrites `infra/.env`, restarts only `instance-manager` via Compose so future JIT provisions use the new default image, and—when explicitly requested—recreates existing `openclaw-*` containers by inspecting each container, preserving its binds/env/labels/network/cmd/resources, swapping only the image reference, and restoring the original running state.

**Tech Stack:** Bash, embedded Python for env/JSON transforms, Docker CLI, shell test harness under `infra/tests/` with a fake `docker` stub.

---

### Task 1: Add a failing script-level regression test

**Files:**
- Create: `infra/tests/set-openclaw-image-check.sh`

**Step 1: Write the failing test**
- Create a temp env file with old `OPENCLAW_IMAGE` / `OPENCLAW_IMAGE_TAG` values.
- Stub `docker` in `PATH` to log commands and return canned outputs for `image inspect`, `compose up`, `ps -a`, `inspect`, `stop`, `rename`, `create`, `start`, and `rm`.
- Run the new script in two modes:
  - defaults-only
  - `--recreate-existing`
- Assert env file values are updated, `instance-manager` is refreshed, and recreate mode issues the expected Docker commands only for `openclaw-*` containers.

**Step 2: Run test to verify it fails**

Run: `bash infra/tests/set-openclaw-image-check.sh`
Expected: FAIL because `ops/set-openclaw-image.sh` does not exist yet.

### Task 2: Implement the image-switch script

**Files:**
- Create: `ops/set-openclaw-image.sh`

**Step 1: Write minimal implementation**
- Parse `--image`, `--tag`, `--env-file`, `--compose-file`, and `--recreate-existing`.
- Rewrite `OPENCLAW_IMAGE` and `OPENCLAW_IMAGE_TAG` in the env file (append if missing).
- Run `docker compose --env-file ... -f ... up -d --no-deps instance-manager`.
- When `--recreate-existing` is set:
  - require the new image to exist locally,
  - enumerate `openclaw-*` containers,
  - recreate each one with preserved binds/env/labels/network/cmd/resources,
  - restore original running/stopped state.

**Step 2: Run test to verify it passes**

Run: `bash infra/tests/set-openclaw-image-check.sh`
Expected: PASS.

### Task 3: Document the new operator flow

**Files:**
- Modify: `README.md`
- Modify: `infra/README.md`

**Step 1: Update docs**
- Describe the new script and its safe default behavior.
- Show how to update to `yx-openclaw:20260308` with and without recreating existing user containers.

**Step 2: Re-run regression test**

Run: `bash infra/tests/set-openclaw-image-check.sh`
Expected: PASS.

### Task 4: Review final diff

**Files:**
- Create: `ops/set-openclaw-image.sh`
- Create: `infra/tests/set-openclaw-image-check.sh`
- Modify: `README.md`
- Modify: `infra/README.md`
- Create: `docs/plans/2026-03-08-set-openclaw-image-implementation-plan.md`

**Step 1: Inspect feature diff**

Run: `git diff -- ops/set-openclaw-image.sh infra/tests/set-openclaw-image-check.sh README.md infra/README.md docs/plans/2026-03-08-set-openclaw-image-implementation-plan.md`
Expected: only the intended script, tests, and docs changes.
