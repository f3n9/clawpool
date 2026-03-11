# Bundled Image Skill Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Bundle an image-generation skill into the container image so OpenClaw can generate images with `qwen-image-2.0` when the user explicitly asks for an image.

**Architecture:** Mirror the existing bundled speech-skill pattern under `infra/docker-build/skills/`. Add a small Node helper that calls the DashScope-compatible image API with env-provided credentials, persists the output into the user workspace, and returns structured JSON that the skill instructions can use to attach or link the generated image.

**Tech Stack:** Bundled OpenClaw skills (`SKILL.md`), Node ESM helper scripts, Python pytest for JIT provisioning checks, Docker image skill copy already present.

---

### Task 1: Lock the bundle contract with tests

**Files:**
- Modify: `services/instance-manager/tests/jit_provision_test.py`
- Reference: `infra/docker-build/Dockerfile`

**Step 1: Write the failing test**

Add a test that asserts:
- `infra/docker-build/skills/image-generate/SKILL.md` exists
- `infra/docker-build/skills/image-generate/generate.mjs` exists
- the helper references `qwen-image-2.0`
- the helper reads env vars such as `OPENCLAW_DASHSCOPE_IMAGE_API_KEY`
- the helper does not embed the provided API key literal
- bundled skills are still copied into `/app/skills`

**Step 2: Run test to verify it fails**

Run: `pytest services/instance-manager/tests/jit_provision_test.py -k bundled_skill -q`
Expected: FAIL because the image skill files do not exist yet.

**Step 3: Write minimal implementation**

Create the image skill files with the smallest behavior needed to satisfy the new assertions.

**Step 4: Run test to verify it passes**

Run: `pytest services/instance-manager/tests/jit_provision_test.py -k bundled_skill -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add services/instance-manager/tests/jit_provision_test.py infra/docker-build/skills/image-generate
 git commit -m "feat: bundle image generation skill"
```

### Task 2: Implement the bundled image skill behavior

**Files:**
- Create: `infra/docker-build/skills/image-generate/SKILL.md`
- Create: `infra/docker-build/skills/image-generate/generate.mjs`
- Reference: `infra/docker-build/skills/asr-transcribe/SKILL.md`
- Reference: `infra/docker-build/skills/tts-synthesize/SKILL.md`

**Step 1: Write the failing test**

Extend the test expectations so the skill contract requires:
- explicit-user-request-only wording in `SKILL.md`
- output saved under workspace-backed storage such as `~/.openclaw/workspace/data/images`
- attachment-first guidance with path/link fallback

**Step 2: Run test to verify it fails**

Run: `pytest services/instance-manager/tests/jit_provision_test.py -k bundled_skill -q`
Expected: FAIL because the new content is not present yet.

**Step 3: Write minimal implementation**

Implement:
- `SKILL.md` instructions that only trigger on explicit image requests
- `generate.mjs` CLI that accepts a prompt, calls the image API, downloads or decodes the returned image, writes it to persistent storage, and prints structured JSON with file metadata

**Step 4: Run test to verify it passes**

Run: `pytest services/instance-manager/tests/jit_provision_test.py -k bundled_skill -q`
Expected: PASS.

**Step 5: Commit**

```bash
git add infra/docker-build/skills/image-generate services/instance-manager/tests/jit_provision_test.py
 git commit -m "feat: add bundled image generation skill"
```

### Task 3: Verify packaging assumptions

**Files:**
- Reference: `infra/docker-build/Dockerfile`

**Step 1: Run focused verification**

Run: `pytest services/instance-manager/tests/jit_provision_test.py -k bundled_skill -q`
Expected: PASS.

**Step 2: Review diff**

Run: `git diff -- services/instance-manager/tests/jit_provision_test.py infra/docker-build/skills/image-generate`
Expected: Only the bundled image skill and related tests are changed.

**Step 3: Commit**

```bash
git add services/instance-manager/tests/jit_provision_test.py infra/docker-build/skills/image-generate
 git commit -m "test: verify bundled image skill packaging"
```
