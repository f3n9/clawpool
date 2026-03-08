# Node Sudo Access Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow the `node` user inside the OpenClaw runtime image to run `sudo` without a password so it can install missing packages interactively with `apt`.

**Architecture:** Keep the privilege change scoped to the custom OpenClaw user-container image in `infra/docker-build/Dockerfile`. Install `sudo`, add a dedicated sudoers drop-in granting `node` `NOPASSWD:ALL`, and verify behavior with a Docker-based regression script that exercises the built image as the `node` user.

**Tech Stack:** Dockerfile, Debian `sudo`, shell-based verification under `infra/tests/`, local Docker build/run commands.

---

### Task 1: Add a failing sudo behavior check

**Files:**
- Create: `infra/tests/openclaw-node-sudo-check.sh`

**Step 1: Write the failing test**

```bash
#!/usr/bin/env bash
set -euo pipefail
IMAGE_TAG="${1:-openclaw-node-sudo-test}"
docker build -t "$IMAGE_TAG" infra/docker-build
docker run --rm --entrypoint sh "$IMAGE_TAG" -lc 'whoami | grep -qx node && sudo -n true'
```

**Step 2: Run test to verify it fails**

Run: `bash infra/tests/openclaw-node-sudo-check.sh`
Expected: FAIL because `sudo` is not installed and/or `node` lacks passwordless sudo.

### Task 2: Add passwordless sudo to the runtime image

**Files:**
- Modify: `infra/docker-build/Dockerfile`

**Step 1: Write minimal implementation**
- Add `sudo` to the apt-installed package list.
- Create `/etc/sudoers.d/node-nopasswd` containing `node ALL=(ALL) NOPASSWD:ALL`.
- Set mode `0440` on the sudoers drop-in.

**Step 2: Run the sudo behavior check**

Run: `bash infra/tests/openclaw-node-sudo-check.sh`
Expected: PASS, proving `node` can run `sudo -n true` in the built image.

### Task 3: Document the container privilege change

**Files:**
- Modify: `infra/README.md`

**Step 1: Update docs**
- State that the OpenClaw runtime image grants passwordless `sudo` to `node`.
- Call out the intended use case: installing missing system packages from inside the user container.

**Step 2: Re-run the behavior check**

Run: `bash infra/tests/openclaw-node-sudo-check.sh`
Expected: PASS.

### Task 4: Review final diff

**Files:**
- Modify: `infra/docker-build/Dockerfile`
- Modify: `infra/README.md`
- Create: `infra/tests/openclaw-node-sudo-check.sh`
- Create: `docs/plans/2026-03-07-node-sudo-implementation-plan.md`

**Step 1: Inspect staged changes**

Run: `git diff -- infra/docker-build/Dockerfile infra/README.md infra/tests/openclaw-node-sudo-check.sh docs/plans/2026-03-07-node-sudo-implementation-plan.md`
Expected: only the intended image, test, and docs changes.
