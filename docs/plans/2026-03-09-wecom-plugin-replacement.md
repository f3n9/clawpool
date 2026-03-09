# WeCom Plugin Replacement Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Swap the bundled WeCom plugin package to `@wecom/wecom-openclaw-plugin` without changing the stable runtime plugin id `wecom`.

**Architecture:** Keep the runtime/plugin reconciliation logic untouched by normalizing the installed plugin directory during the image build. The Docker build becomes responsible for mapping the package install result to `/home/node/.openclaw/extensions/wecom` and `/opt/openclaw/extensions/wecom`.

**Tech Stack:** Dockerfile shell logic, bundled extension copy flow, live Docker verification.

---

### Task 1: Update the image build package install
- Modify `infra/docker-build/Dockerfile`
- Replace the old install command with `openclaw plugins install @wecom/wecom-openclaw-plugin`
- Detect the installed extension directory and normalize it to `wecom`

### Task 2: Keep bundled-copy behavior stable
- Modify `infra/docker-build/Dockerfile`
- Ensure `/opt/openclaw/extensions/wecom` still exists after build
- Avoid assuming the source directory name equals the npm package name

### Task 3: Verify
- Rebuild the custom image
- Inspect the bundled extension path inside the image
- Confirm the runtime still shows `WeCom` enabled for a fresh user container
