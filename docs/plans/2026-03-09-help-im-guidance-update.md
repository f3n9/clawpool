# Help IM Guidance Update Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Update the public `/help` page so the IM guidance section prominently links to the official WeCom setup guide and clearly recommends Telegram/Discord first because WeCom file receiving is not yet supported.

**Architecture:** Keep the change scoped to the existing help page HTML generator in the instance-manager service and extend the focused unit tests that already verify `/help` page content. No routing or asset changes are needed.

**Tech Stack:** Python 3.12, `unittest`, static HTML emitted from `services_instance_manager.main`

---

### Task 1: Add failing content assertions

**Files:**
- Modify: `services/instance-manager/tests/jit_provision_test.py`

**Step 1: Write the failing test**
- Extend the existing `/help` page content test with assertions for:
  - the WeCom limitation notice
  - the Telegram/Discord recommendation
  - the official WeCom documentation URL
  - the CTA label for the documentation button

**Step 2: Run test to verify it fails**
Run: `PYTHONPATH=/home/fyue/git/clawpool/services/instance-manager python3 -m unittest services.instance-manager.tests.jit_provision_test.HelpPageTests.test_help_page_contains_navigation_and_guidance -v`
Expected: FAIL because the new strings do not exist yet.

### Task 2: Update help page HTML

**Files:**
- Modify: `services/instance-manager/services_instance_manager/main.py`

**Step 1: Write minimal implementation**
- Update the IM guidance section to add:
  - a prominent warning/tip about current WeCom file receive limitations
  - a recommendation to prefer Telegram/Discord
  - a visible button linking to the official WeCom setup guide

**Step 2: Run test to verify it passes**
Run: `PYTHONPATH=/home/fyue/git/clawpool/services/instance-manager python3 -m unittest services.instance-manager.tests.jit_provision_test.HelpPageTests.test_help_page_contains_navigation_and_guidance -v`
Expected: PASS.

### Task 3: Verify broader coverage

**Files:**
- Modify: none unless follow-up fixes are needed

**Step 1: Run focused suite**
Run: `PYTHONPATH=/home/fyue/git/clawpool/services/instance-manager python3 -m unittest services.instance-manager.tests.jit_provision_test -v`
Expected: PASS.

**Step 2: Commit**
Run:
`git add docs/plans/2026-03-09-help-im-guidance-update.md services/instance-manager/tests/jit_provision_test.py services/instance-manager/services_instance_manager/main.py`
`git commit -m "docs: clarify help page IM guidance"`
