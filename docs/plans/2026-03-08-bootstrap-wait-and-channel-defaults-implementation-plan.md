# Bootstrap Wait Page And Channel Defaults Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix the first-login bootstrap wait page regression and then correct default channel/plugin persistence so only extra bundled plugins like `wecom` are explicitly enabled in runtime config.

**Architecture:** Implement the two fixes as separate, test-first changes. First, adjust the wait-page gate so browser navigation through `/resolve` can render the bootstrap page. Second, split built-in channel defaults from extra bundled plugins by limiting explicit runtime persistence to bundled plugin roots such as `/opt/openclaw/extensions` while preserving explicit user overrides.

**Tech Stack:** Python 3.12, `unittest`, `services_instance_manager.main`, runtime config reconciliation logic.

---

### Task 1: Fix `/resolve` bootstrap wait page behavior

**Files:**
- Modify: `services/instance-manager/tests/jit_provision_test.py`
- Modify: `services/instance-manager/services_instance_manager/main.py`

**Step 1: Write the failing test**

```python
def test_resolve_uses_bootstrap_wait_page_for_browser_navigation(self):
    ...
```

**Step 2: Run targeted test to verify it fails**

Run: `PYTHONPATH=services/instance-manager python3 -m unittest services/instance-manager/tests/jit_provision_test.py`
Expected: FAIL because `/resolve` is currently excluded from wait-page rendering.

**Step 3: Write minimal implementation**
- Update `_should_use_bootstrap_wait_page()` so `/resolve` behaves like a browser-navigation entrypoint.
- Keep `/health` and `/__openclaw__/bootstrap-status` excluded.

**Step 4: Run targeted test to verify it passes**

Run: `PYTHONPATH=services/instance-manager python3 -m unittest services/instance-manager/tests/jit_provision_test.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add services/instance-manager/tests/jit_provision_test.py services/instance-manager/services_instance_manager/main.py
git commit -m "fix: show bootstrap wait page during resolve"
```

### Task 2: Persist only extra bundled channel plugins by default

**Files:**
- Modify: `services/instance-manager/tests/jit_provision_test.py`
- Modify: `services/instance-manager/services_instance_manager/main.py`
- Modify: `infra/README.md`

**Step 1: Write the failing tests**

```python
def test_builtin_channel_defaults_are_not_persisted_explicitly(self):
    ...

def test_bundled_extra_channel_plugins_still_persist_enabled(self):
    ...
```

**Step 2: Run targeted test to verify it fails**

Run: `PYTHONPATH=services/instance-manager python3 -m unittest services/instance-manager/tests/jit_provision_test.py`
Expected: FAIL because `telegram` is still explicitly written and built-in roots are still part of explicit persistence.

**Step 3: Write minimal implementation**
- Restrict default explicit persistence to extra bundled plugin roots.
- Remove built-in channel IDs from the default explicit fallback set.
- Preserve explicit `enabled: false` values.

**Step 4: Run targeted test to verify it passes**

Run: `PYTHONPATH=services/instance-manager python3 -m unittest services/instance-manager/tests/jit_provision_test.py`
Expected: PASS.

**Step 5: Commit**

```bash
git add services/instance-manager/tests/jit_provision_test.py services/instance-manager/services_instance_manager/main.py infra/README.md
git commit -m "fix: persist only bundled extra channel plugins"
```
