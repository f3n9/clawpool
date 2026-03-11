# Files Route Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a secure `/files/<path>` route to `instance-manager` so the currently authenticated user can download files from their own `~/.openclaw/workspace/` tree.

**Architecture:** Resolve the authenticated identity using the existing routing flow, map the request to the host-mounted runtime directory for that identity, constrain access to `runtime/workspace/`, and stream the file directly from `instance-manager`. Do not proxy through the user container.

**Tech Stack:** Python `http.server`, existing identity resolution in `services_instance_manager.main`, host bind-mounted runtime directories, `unittest`-based tests.

---

### Task 1: Lock route behavior with tests

**Files:**
- Modify: `services/instance-manager/tests/jit_provision_test.py`
- Reference: `services/instance-manager/services_instance_manager/main.py`

**Step 1: Write the failing test**

Add tests covering:
- authenticated `/files/path/to/file.png` maps to `<users_root>/<identity>/runtime/workspace/path/to/file.png`
- unauthenticated requests are rejected or redirected through existing auth behavior
- path traversal and absolute paths are rejected
- missing files return 404

**Step 2: Run test to verify it fails**

Run: `PYTHONPATH=services/instance-manager python3 -m unittest services/instance-manager/tests/jit_provision_test.py`
Expected: FAIL because `/files` is not implemented.

**Step 3: Write minimal implementation**

Add the smallest route and helper functions needed to satisfy the tests.

**Step 4: Run test to verify it passes**

Run: `PYTHONPATH=services/instance-manager python3 -m unittest services/instance-manager/tests/jit_provision_test.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add services/instance-manager/services_instance_manager/main.py services/instance-manager/tests/jit_provision_test.py docs/plans/2026-03-11-files-route-design.md
 git commit -m "feat: add authenticated workspace file downloads"
```

### Task 2: Deploy and validate instance-manager

**Files:**
- Reference: `infra/docker-compose.base.yml`

**Step 1: Restart instance-manager**

Run the deployment command already used for this environment to rebuild/restart `infra-instance-manager-1`.

**Step 2: Validate route**

Check a known file path through `/files/...` for an authenticated user.

**Step 3: Commit operational notes if needed**

Only if deployment requires durable docs changes.
