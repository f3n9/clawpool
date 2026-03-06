ARG OPENCLAW_BASE_IMAGE=ghcr.io/openclaw/openclaw:latest@sha256:d60f848db7d5019336dfa17412588881a42774ad34a3429f03dd06d0a71c2848
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
  && rm -rf /var/lib/apt/lists/*

ARG INSTALL_PLAYWRIGHT=0
RUN if [ "$INSTALL_PLAYWRIGHT" = "1" ]; then \
      node /app/node_modules/playwright-core/cli.js install chromium; \
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
