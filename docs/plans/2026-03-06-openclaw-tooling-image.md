# OpenClaw Tooling Image Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add grouped build-time install switches and a broad default toolset to the custom OpenClaw image, then document the new capabilities.

**Architecture:** Keep one Dockerfile and build everything from Debian packages. Group packages behind build args that default to enabled so the current deployment stays "full-fat" while future builds can selectively slim down.

**Tech Stack:** Docker, Debian apt packages, existing OpenClaw custom image build.

---

### Task 1: Add grouped build args

**Files:**
- Modify: `infra/docker-build/Dockerfile`

**Step 1:** Add `ARG INSTALL_*` switches for OCR, LibreOffice, PDF, image, media, scraping, archive, and analysis groups.

**Step 2:** Replace the fixed apt package line with a grouped package accumulator in one `RUN` block.

**Step 3:** Keep current browser runtime dependencies and existing Playwright install logic intact.

**Step 4:** Verify the Dockerfile still parses by running a full image build.

### Task 2: Add requested tooling packages

**Files:**
- Modify: `infra/docker-build/Dockerfile`

**Step 1:** Add OCR packages including English and simplified Chinese language packs.

**Step 2:** Add Office/PDF conversion packages including `libreoffice`.

**Step 3:** Add image/media/archive/source-analysis packages requested in the design.

**Step 4:** Avoid fragile packages that are likely unavailable on the default Debian repositories.

### Task 3: Document the image behavior

**Files:**
- Modify: `infra/README.md`
- Modify: `README.md`

**Step 1:** Document the custom image tool groups and the default-enabled build args.

**Step 2:** Add an example build command for the local image.

**Step 3:** Mention that turning groups off is supported for future image slimming.

### Task 4: Validate

**Files:**
- Verify: `infra/docker-build/Dockerfile`

**Step 1:** Build the image locally with defaults.

**Step 2:** Run a quick command-presence smoke check against representative binaries.

**Step 3:** Summarize any package substitutions made for repository compatibility.
