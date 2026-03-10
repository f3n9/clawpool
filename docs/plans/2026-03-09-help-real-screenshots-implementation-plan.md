# Help Real Screenshots Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the placeholder SVG illustrations on the public `/help` page with real screenshots of the Dashboard and `/console` so first-time users see the actual product UI.

**Architecture:** Keep the existing `/help` page structure and only swap the static help assets plus the asset references in the instance-manager HTML. Capture screenshots from the live product for a real user session if feasible; otherwise use the closest production-equivalent render path that preserves the actual UI. Verify with focused unit tests and a live `/help` fetch.

**Tech Stack:** Python 3.12, `unittest`, static asset serving from `services_instance_manager`, shell tooling for image capture/optimization, Docker runtime where needed

---

### Task 1: Confirm screenshot source and paths

**Files:**
- Review: `services/instance-manager/services_instance_manager/main.py`
- Review: `services/instance-manager/tests/jit_provision_test.py`
- Review: current assets under `services/instance-manager/services_instance_manager/static/help/`
- Create: `docs/plans/2026-03-09-help-real-screenshots-implementation-plan.md`

**Step 1: Discover the best screenshot capture path**
- Inspect routing/auth behavior for Dashboard and `/console`
- Prefer a real production render path for an existing user environment
- Record exact commands needed to capture the images

**Step 2: Verify asset naming strategy**
- Keep the two existing logical asset roles (`dashboard-overview`, `console-overview`)
- Update file extension and references only if needed

### Task 2: Write the failing test

**Files:**
- Modify: `services/instance-manager/tests/jit_provision_test.py`

**Step 1: Write the failing test**
- Update help asset existence assertions so they require the final real-image filenames instead of the current SVG placeholders
- If references change, assert the help HTML points at the new asset filenames

**Step 2: Run test to verify it fails**
Run: `PYTHONPATH=/home/fyue/git/clawpool/services/instance-manager python3 -m unittest services.instance-manager.tests.jit_provision_test.JITProvisionTests.test_help_static_assets_exist services.instance-manager.tests.jit_provision_test.JITProvisionTests.test_help_page_contains_navigation_and_guidance -v`
Expected: FAIL until the new assets and/or references exist.

### Task 3: Replace help assets and references

**Files:**
- Add or replace: `services/instance-manager/services_instance_manager/static/help/dashboard-overview.*`
- Add or replace: `services/instance-manager/services_instance_manager/static/help/console-overview.*`
- Modify: `services/instance-manager/services_instance_manager/main.py`

**Step 1: Capture and optimize real screenshots**
- Produce one Dashboard screenshot and one `/console` screenshot
- Optimize size while keeping text readable

**Step 2: Update help page references**
- Point image tags to the new asset filenames
- Keep alt text and surrounding copy intact unless minor wording adjustments are needed

**Step 3: Run focused tests to verify they pass**
Run: `PYTHONPATH=/home/fyue/git/clawpool/services/instance-manager python3 -m unittest services.instance-manager.tests.jit_provision_test.JITProvisionTests.test_help_static_assets_exist services.instance-manager.tests.jit_provision_test.JITProvisionTests.test_help_page_contains_navigation_and_guidance -v`
Expected: PASS.

### Task 4: Verify broader behavior and publish

**Files:**
- Modify: none unless follow-up fixes are needed

**Step 1: Run focused suite**
Run: `PYTHONPATH=/home/fyue/git/clawpool/services/instance-manager python3 -m unittest services.instance-manager.tests.jit_provision_test -v`
Expected: PASS.

**Step 2: Commit**
Run:
`git add docs/plans/2026-03-09-help-real-screenshots-implementation-plan.md services/instance-manager/services_instance_manager/main.py services/instance-manager/services_instance_manager/static/help/ services/instance-manager/tests/jit_provision_test.py`
`git commit -m "docs: replace help illustrations with real screenshots"`

**Step 3: Deploy and verify live**
Run: `docker compose --env-file infra/.env -f infra/docker-compose.base.yml up -d --build instance-manager`
Then verify:
- `curl -fsSL https://claw.hatch.yinxiang.com/help`
- `curl -fsSLI https://claw.hatch.yinxiang.com/help/assets/<asset-name>`
