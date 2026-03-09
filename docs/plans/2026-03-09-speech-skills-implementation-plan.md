# Speech Skills Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Preinstall `asr-transcribe` and `tts-synthesize` system skills into the OpenClaw image and load them by default for new user containers.

**Architecture:** Add two image-bundled skills plus minimal helper scripts, update runtime config defaults to load both `/app/skills` and the persisted workspace skills directory, and cover the change with targeted tests for packaging and config seeding.

**Tech Stack:** Docker, Python, unittest, markdown skills, shell/Node/Python helper scripts

---

### Task 1: Add failing tests for default system skill loading

**Files:**
- Modify: `services/instance-manager/tests/jit_provision_test.py`
- Modify: `services/instance-manager/services_instance_manager/main.py`

**Step 1: Write the failing test**
- Assert new runtime config includes both `/app/skills` and `~/.openclaw/workspace/skills` in `skills.load.extraDirs`.
- Assert explicit user-provided skill directories are preserved when already configured.

**Step 2: Run test to verify it fails**
Run: `PYTHONPATH=/home/fyue/git/clawpool/services/instance-manager python3 -m unittest services.instance-manager.tests.jit_provision_test -v`
Expected: FAIL because only workspace skills are currently seeded.

**Step 3: Write minimal implementation**
- Update runtime config seeding to include system skill dir plus workspace skill dir.

**Step 4: Run test to verify it passes**
Run the same unittest command.
Expected: PASS.

**Step 5: Commit**
```bash
git add services/instance-manager/services_instance_manager/main.py services/instance-manager/tests/jit_provision_test.py
git commit -m "feat: load bundled system skills by default"
```

### Task 2: Add bundled speech skill directories

**Files:**
- Create: `infra/docker-build/skills/asr-transcribe/SKILL.md`
- Create: `infra/docker-build/skills/asr-transcribe/<helper scripts>`
- Create: `infra/docker-build/skills/tts-synthesize/SKILL.md`
- Create: `infra/docker-build/skills/tts-synthesize/<helper scripts>`

**Step 1: Write the failing test**
- Add checks that build assets for both skills exist and reference the expected model names and env vars.

**Step 2: Run test to verify it fails**
Run targeted validation.
Expected: FAIL because the skill files do not exist yet.

**Step 3: Write minimal implementation**
- Add the two bundled skill directories and helper scripts.
- Keep API keys environment-driven only.

**Step 4: Run test to verify it passes**
Run the targeted validation again.
Expected: PASS.

**Step 5: Commit**
```bash
git add infra/docker-build/skills
git commit -m "feat: add bundled speech skills"
```

### Task 3: Copy bundled speech skills into the image

**Files:**
- Modify: `infra/docker-build/Dockerfile`
- Test: `services/instance-manager/tests/jit_provision_test.py` or `infra/tests/...`

**Step 1: Write the failing test**
- Assert Docker build assets are copied into `/app/skills`.

**Step 2: Run test to verify it fails**
Run the targeted test.
Expected: FAIL because the Dockerfile does not install the skills yet.

**Step 3: Write minimal implementation**
- Copy bundled skill directories into `/app/skills` during Docker build.
- Ensure resulting files are readable by the `node` user.

**Step 4: Run test to verify it passes**
Run the targeted test again.
Expected: PASS.

**Step 5: Commit**
```bash
git add infra/docker-build/Dockerfile services/instance-manager/tests/jit_provision_test.py
git commit -m "build: bundle speech skills into the image"
```

### Task 4: Verify end-to-end docs and safety

**Files:**
- Modify: `infra/README.md` (if documenting required env vars)
- Modify: `docs/plans/2026-03-09-speech-skills-design.md` if implementation details changed

**Step 1: Validate tests**
Run: `PYTHONPATH=/home/fyue/git/clawpool/services/instance-manager python3 -m unittest services.instance-manager.tests.jit_provision_test -v`
Expected: PASS.

**Step 2: Validate no secrets are committed**
Run: `git diff --cached`
Expected: only env var names, no real API key values.

**Step 3: Final commit**
```bash
git add infra/README.md docs/plans/2026-03-09-speech-skills-design.md docs/plans/2026-03-09-speech-skills-implementation-plan.md
git commit -m "docs: plan bundled speech skills"
```
