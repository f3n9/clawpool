# Bundled Channel Defaults Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Enable all built-in OpenClaw channel extensions and all bundled image plugins by default for each provisioned user, while preserving any user-explicit disable flags.

**Architecture:** Keep the existing instance-manager runtime config reconciliation, but broaden it to merge discovered plugin IDs from configurable extension roots when available. Add a startup-time reconciliation step inside the OpenClaw container so image-local extension roots such as `/app/extensions` and `/opt/openclaw/extensions` are scanned before the app starts, ensuring the defaults match the actual image contents.

**Tech Stack:** Python 3.12 (`services_instance_manager`), `unittest`, Docker shell startup command, inline Node.js config reconciliation.

---

### Task 1: Cover discovery and merge behavior with failing tests

**Files:**
- Modify: `services/instance-manager/tests/jit_provision_test.py`

**Step 1: Write the failing tests**

```python
def test_discovered_channel_plugins_default_enabled_without_overriding_explicit_false(self):
    ...

def test_invalid_discovered_plugin_names_are_ignored(self):
    ...

def test_default_startup_cmd_reconciles_image_plugin_roots(self):
    ...
```

**Step 2: Run the targeted test file to verify failures**

Run: `python -m unittest services/instance-manager/tests/jit_provision_test.py`
Expected: FAIL because discovery helpers and startup reconciliation do not exist yet.

### Task 2: Implement host-side discovery and config merge helpers

**Files:**
- Modify: `services/instance-manager/services_instance_manager/main.py`

**Step 1: Add minimal helpers**
- Add plugin ID validation helper.
- Add filesystem discovery helper for configurable plugin roots.
- Add a merge helper that enables discovered/default plugins only when `enabled` is not already a boolean.

**Step 2: Wire helpers into runtime config reconciliation**
- Replace the current `telegram,wecom`-only loop with merged defaults from explicit env IDs plus discovered local roots.

**Step 3: Run targeted tests**

Run: `python -m unittest services/instance-manager/tests/jit_provision_test.py`
Expected: PASS for the new helper-driven behavior.

### Task 3: Reconcile image-driven defaults at container startup

**Files:**
- Modify: `services/instance-manager/services_instance_manager/main.py`

**Step 1: Extend default startup command**
- Keep copying bundled runtime extensions.
- Add a pre-launch Node.js snippet that scans `/app/extensions` and `/opt/openclaw/extensions`, merges valid plugin IDs into `/home/node/.openclaw/openclaw.json`, and preserves explicit user booleans.

**Step 2: Propagate override env vars into user containers**
- Pass through `OPENCLAW_DEFAULT_CHANNEL_PLUGINS` and `OPENCLAW_DEFAULT_CHANNEL_PLUGIN_DIRS` when configured.

**Step 3: Re-run targeted tests**

Run: `python -m unittest services/instance-manager/tests/jit_provision_test.py`
Expected: PASS, including startup command assertions.

### Task 4: Document the new default-discovery behavior

**Files:**
- Modify: `infra/docker-compose.base.yml`
- Modify: `infra/README.md`

**Step 1: Document env defaults**
- Note that built-in and bundled plugins are auto-enabled from image roots.
- Document `OPENCLAW_DEFAULT_CHANNEL_PLUGIN_DIRS` as the discovery override.

**Step 2: Verify docs/config consistency**

Run: `git diff -- services/instance-manager/services_instance_manager/main.py services/instance-manager/tests/jit_provision_test.py infra/docker-compose.base.yml infra/README.md docs/plans/2026-03-07-bundled-channel-defaults-implementation-plan.md`
Expected: shows only the intended feature changes.
