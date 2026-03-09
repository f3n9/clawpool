#ARG OPENCLAW_BASE_IMAGE=ghcr.io/openclaw/openclaw:2026.3.2
ARG OPENCLAW_BASE_IMAGE=ghcr.io/openclaw/openclaw:2026.3.7-slim-amd64
FROM ${OPENCLAW_BASE_IMAGE}

USER root

ARG DEBIAN_FRONTEND=noninteractive
ARG INSTALL_OCR=1
ARG INSTALL_LIBREOFFICE=1
ARG INSTALL_PDF=1
ARG INSTALL_IMAGE=1
ARG INSTALL_MEDIA=1
ARG INSTALL_SCRAPING=1
ARG INSTALL_ANALYSIS=1
ARG INSTALL_ARCHIVE=1
RUN set -eux; \
    apt-get update; \
    packages=' \
      git \
      curl \
      wget \
      jq \
      sudo \
      ripgrep \
      fd-find \
      less \
      vim \
      nano \
      procps \
      iproute2 \
      iputils-ping \
      dnsutils \
      netcat-openbsd \
      telnet \
      traceroute \
      lsof \
      strace \
      build-essential \
      make \
      patch \
      diffutils \
      file \
      tree \
      python3 \
      python3-pip \
      python3-venv \
      ffmpeg \
      ca-certificates \
      fonts-noto \
      fonts-noto-cjk \
      fonts-noto-color-emoji \
      fonts-liberation \
      libxss1 \
      libgtk-3-0 \
      libgbm1 \
      libasound2 \
      libnss3 \
      libx11-xcb1 \
      libxcomposite1 \
      libxdamage1 \
      libxrandr2 \
      libatk1.0-0 \
      libatk-bridge2.0-0 \
      libpangocairo-1.0-0 \
      libxshmfence1 \
      libdrm2 \
    '; \
    if [ "$INSTALL_OCR" = "1" ]; then \
      packages="$packages tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim"; \
    fi; \
    if [ "$INSTALL_LIBREOFFICE" = "1" ]; then \
      packages="$packages libreoffice libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-draw"; \
    fi; \
    if [ "$INSTALL_PDF" = "1" ]; then \
      packages="$packages pandoc poppler-utils ghostscript qpdf mupdf-tools wkhtmltopdf ocrmypdf img2pdf antiword odt2txt catdoc"; \
    fi; \
    if [ "$INSTALL_IMAGE" = "1" ]; then \
      packages="$packages imagemagick graphicsmagick pngquant jpegoptim optipng webp libimage-exiftool-perl"; \
    fi; \
    if [ "$INSTALL_MEDIA" = "1" ]; then \
      packages="$packages mediainfo"; \
    fi; \
    if [ "$INSTALL_SCRAPING" = "1" ]; then \
      packages="$packages lynx html2text"; \
    fi; \
    if [ "$INSTALL_ANALYSIS" = "1" ]; then \
      packages="$packages silversearcher-ag universal-ctags"; \
    fi; \
    if [ "$INSTALL_ARCHIVE" = "1" ]; then \
      packages="$packages zip unzip p7zip-full xz-utils zstd bzip2 lz4 unar cabextract"; \
    fi; \
    apt-get install -y --no-install-recommends $packages; \
    if ! command -v fd >/dev/null 2>&1 && command -v fdfind >/dev/null 2>&1; then ln -sf "$(command -v fdfind)" /usr/local/bin/fd; fi; \
    if ! command -v magick >/dev/null 2>&1 && command -v convert >/dev/null 2>&1; then ln -sf "$(command -v convert)" /usr/local/bin/magick; fi; \
    install -d -m 0755 /etc/sudoers.d; \
    printf 'node ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/node-nopasswd; \
    chmod 0440 /etc/sudoers.d/node-nopasswd; \
    rm -rf /var/lib/apt/lists/*

ARG INSTALL_PLAYWRIGHT=1
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN if [ "$INSTALL_PLAYWRIGHT" = "1" ]; then \
      node /app/node_modules/playwright-core/cli.js install chromium; \
      CHROME_BIN="$(find "${PLAYWRIGHT_BROWSERS_PATH}" -type f \( -path "*/chrome-linux64/chrome" -o -path "*/chrome-linux/chrome" \) | head -n1)"; \
      if [ -n "${CHROME_BIN}" ]; then ln -sf "${CHROME_BIN}" /usr/local/bin/openclaw-chromium; fi; \
      chown -R node:node "${PLAYWRIGHT_BROWSERS_PATH}"; \
    fi

USER node

ENV HOME=/home/node
ARG NPM_REGISTRY=https://registry.npmjs.org
ENV NPM_CONFIG_REGISTRY=${NPM_REGISTRY}
# Install plugin into the node user's OpenClaw home/extensions area
RUN set -eux; \
  openclaw plugins install @wecom/wecom-openclaw-plugin; \
  src_dir="$({ find /home/node/.openclaw/extensions -mindepth 1 -maxdepth 1 -type d | while read -r candidate; do if [ -f "$candidate/openclaw.plugin.json" ] && grep -q '"id"[[:space:]]*:[[:space:]]*"wecom"' "$candidate/openclaw.plugin.json"; then printf '%s\n' "$candidate"; break; fi; done; } || true)"; \
  if [ -z "$src_dir" ]; then \
    src_dir="$({ find /home/node/.openclaw/extensions -mindepth 1 -maxdepth 1 -type d \( -name 'wecom' -o -name 'wecom-openclaw-plugin' \) | head -n1; } || true)"; \
  fi; \
  [ -n "$src_dir" ]; \
  if [ "$(basename "$src_dir")" != "wecom" ]; then \
    rm -rf /home/node/.openclaw/extensions/wecom; \
    mv "$src_dir" /home/node/.openclaw/extensions/wecom; \
  fi; \
  if grep -Rqs "@wecom/aibot-node-sdk" /home/node/.openclaw/extensions/wecom/dist; then \
    if [ ! -d /home/node/.openclaw/extensions/wecom/node_modules/@wecom/aibot-node-sdk ]; then \
      npm install --prefix /home/node/.openclaw/extensions/wecom --no-save @wecom/aibot-node-sdk; \
    fi; \
  fi

USER root
# Keep a bundled copy outside /home/node/.openclaw (runtime bind mount)
RUN mkdir -p /opt/openclaw/extensions \
  && rm -rf /opt/openclaw/extensions/wecom \
  && cp -a /home/node/.openclaw/extensions/wecom /opt/openclaw/extensions/wecom \
  && chown -R node:node /opt/openclaw

COPY docker-entrypoint-with-extensions.sh /usr/local/bin/docker-entrypoint-with-extensions.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint-with-extensions.sh

USER node
ENTRYPOINT ["docker-entrypoint-with-extensions.sh"]
