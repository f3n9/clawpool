# Enable Model Reasoning Defaults Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure every configured model is initialized with reasoning mode enabled by default.

**Architecture:** Update the runtime config seeding in `services_instance_manager/main.py` so all provider model definitions default `reasoning` to `true`, and stop pruning reasoning-specific params during initialization. Cover the behavior with focused unit tests in `jit_provision_test.py`.

**Tech Stack:** Python, unittest, instance-manager runtime config generation

---

### Task 1: Add regression tests for reasoning defaults

**Files:**
- Modify: `services/instance-manager/tests/jit_provision_test.py`
- Test: `services/instance-manager/tests/jit_provision_test.py`

**Step 1: Write the failing test**
- Add assertions that all configured OpenAI and DashScope models have `reasoning == True` after initialization.
- Add assertions that seeded model params keep `reasoningEffort` and `reasoningSummary` entries when present.

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=/home/fyue/git/clawpool/services/instance-manager python3 -m unittest <targeted tests> -v`
Expected: FAIL because current seeding disables reasoning for some models and strips reasoning params.

**Step 3: Write minimal implementation**
- Update provider model generation to set reasoning enabled for all configured models.
- Remove initialization-time pruning of reasoning params.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=/home/fyue/git/clawpool/services/instance-manager python3 -m unittest <targeted tests> -v`
Expected: PASS

**Step 5: Commit**

```bash
git add services/instance-manager/services_instance_manager/main.py services/instance-manager/tests/jit_provision_test.py docs/plans/2026-03-09-enable-model-reasoning-defaults.md
git commit -m "fix: enable reasoning for all seeded models"
```
