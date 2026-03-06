ARG OPENCLAW_BASE_IMAGE=ghcr.io/openclaw/openclaw:main@sha256:75b4b1732478dbf8818ff2297326b8907867322695ffd8a3e8f2500846e1e74a
FROM ${OPENCLAW_BASE_IMAGE}

USER root

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    wget \
    jq \
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
  && rm -rf /var/lib/apt/lists/*

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
RUN openclaw plugins install @yanhaidao/wecom

USER root
# Keep a bundled copy outside /home/node/.openclaw (runtime bind mount)
RUN mkdir -p /opt/openclaw/extensions \
  && cp -a /home/node/.openclaw/extensions/wecom /opt/openclaw/extensions/wecom \
  && chown -R node:node /opt/openclaw

COPY docker-entrypoint-with-extensions.sh /usr/local/bin/docker-entrypoint-with-extensions.sh
RUN chmod 0755 /usr/local/bin/docker-entrypoint-with-extensions.sh

USER node
ENTRYPOINT ["docker-entrypoint-with-extensions.sh"]
