# Dual OpenAI-Compatible Provider Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a second OpenAI-compatible provider named `dashscope`, preserve the existing `openai` provider, and make `dashscope/MiniMax-M2.5` the default model.

**Architecture:** Extend runtime config reconciliation so provider-prefixed model refs are preserved instead of coerced to `openai/*`. Build the `openai` provider from existing env/defaults, and build a new `dashscope` provider from fixed-compatible endpoint defaults plus explicit model IDs. Update agent defaults and provider entries together so existing OpenAI models remain intact.

**Tech Stack:** Python runtime config generation, JSON config reconciliation, Python `unittest`.

---

### Task 1: Lock dual-provider behavior with tests

**Files:**
- Modify: `services/instance-manager/tests/jit_provision_test.py`

**Step 1: Write the failing tests**
- Add a test that verifies a new runtime config contains both `models.providers.openai` and `models.providers.dashscope`.
- Add a test that verifies `agents.defaults.model.primary` becomes `dashscope/MiniMax-M2.5`.
- Add a test that verifies existing OpenAI provider models remain present.

**Step 2: Run test to verify it fails**
Run: `PYTHONPATH=services/instance-manager python3 -m unittest services/instance-manager/tests/jit_provision_test.py`
Expected: FAIL on missing `dashscope` provider and wrong default primary.

### Task 2: Implement provider-aware model normalization

**Files:**
- Modify: `services/instance-manager/services_instance_manager/main.py`

**Step 1: Preserve provider prefixes**
- Stop coercing every unqualified model into `openai/*` without considering multi-provider defaults.
- Keep unqualified legacy allowed models mapped to `openai/*` for backward compatibility.
- Support explicit `dashscope/*` entries cleanly.

**Step 2: Add DashScope provider generation**
- Build `models.providers.dashscope` with the supplied endpoint, env-provided API key, API mode, and model IDs.
- Keep `models.providers.openai` generation intact.

**Step 3: Keep agent model params aligned**
- Ensure `agents.defaults.models` includes entries for both `openai/*` and `dashscope/*` models.
- Keep transport defaults consistent.

### Task 3: Verify and clean up

**Files:**
- Modify: `services/instance-manager/tests/jit_provision_test.py`
- Modify: `services/instance-manager/services_instance_manager/main.py`

**Step 1: Run targeted tests**
Run: `PYTHONPATH=services/instance-manager python3 -m unittest services/instance-manager/tests/jit_provision_test.py`
Expected: PASS

**Step 2: Run related startup tests**
Run: `PYTHONPATH=services/instance-manager python3 -m unittest services/instance-manager/tests/startup_test.py services/instance-manager/tests/jit_provision_test.py`
Expected: PASS

**Step 3: Commit**
```bash
git add services/instance-manager/services_instance_manager/main.py services/instance-manager/tests/jit_provision_test.py docs/plans/2026-03-08-dual-openai-compatible-provider-design.md docs/plans/2026-03-08-dual-openai-compatible-provider.md
git commit -m "feat: add dashscope-compatible model provider"
```
