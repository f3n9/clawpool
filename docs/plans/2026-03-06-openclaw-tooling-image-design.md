# OpenClaw Tooling Image Design

**Goal:** Expand the custom OpenClaw runtime image so each per-user container ships with a broad set of OCR, Office/PDF conversion, image/media processing, archive, scraping, and source-analysis tools out of the box.

## Decisions

- Keep a single custom image based on the official OpenClaw image.
- Use build args to segment heavy tool groups, but default every group to enabled.
- Prefer Debian packages for reproducibility and simpler maintenance.
- Keep existing Playwright/Chromium and bundled `wecom` plugin behavior unchanged.
- Exclude clearly security-sensitive tooling categories that were not requested.

## Tool Groups

- OCR: `tesseract` and common language packs.
- Office/PDF: `libreoffice`, `pandoc`, `poppler`, `ghostscript`, `qpdf`, `mupdf`.
- Image: `imagemagick`, `graphicsmagick`, `pngquant`, `optipng`, `jpegoptim`, `webp`.
- Media: `ffmpeg`, `mediainfo`.
- Scraping: `curl`, `wget`, `lynx`, `html2text`, headless Chromium.
- Archive: `zip`, `unzip`, `7z`, `xz`, `zstd`, `bzip2`, `lz4`, `unar`, `cabextract`.
- Source analysis: `rg`, `fd`, `jq`, `tree`, `file`, `make`, `patch`, `diffutils`, `ag`, `ctags`.

## Constraints

- Image size and build time will increase substantially, especially with `libreoffice`.
- Package list should avoid fragile or non-free-only dependencies where possible.
- Documentation must explain the build args and the major commands available in-container.
