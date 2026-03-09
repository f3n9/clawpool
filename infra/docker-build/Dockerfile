#ARG OPENCLAW_BASE_IMAGE=ghcr.io/openclaw/openclaw:2026.3.2
ARG OPENCLAW_BASE_IMAGE=ghcr.io/openclaw/openclaw:2026.3.8-slim-amd64
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

USER root

ENV HOME=/home/node
ARG NPM_REGISTRY=https://registry.npmjs.org
ENV NPM_CONFIG_REGISTRY=${NPM_REGISTRY}
# Install the official WeCom plugin as an image-bundled built-in extension.
RUN set -eux; \
  tmp_home="$(mktemp -d)"; \
  tmp_pack="$(mktemp -d)"; \
  rm -rf /app/extensions/wecom-openclaw-plugin; \
  mkdir -p /app/extensions/wecom-openclaw-plugin; \
  HOME="$tmp_home" npm pack --pack-destination "$tmp_pack" @wecom/wecom-openclaw-plugin; \
  tar -xzf "$tmp_pack"/wecom-wecom-openclaw-plugin-*.tgz --strip-components=1 -C /app/extensions/wecom-openclaw-plugin package; \
  runtime_deps="$(jq -r '.dependencies // {} | to_entries[] | select(.key != "@wecom/wecom-openclaw-plugin") | "\(.key)@\(.value)"' /app/extensions/wecom-openclaw-plugin/package.json | paste -sd ' ' -)"; \
  if [ -n "$runtime_deps" ]; then \
    npm install --prefix /app/extensions/wecom-openclaw-plugin --no-save $runtime_deps; \
  fi; \
  if grep -Rqs "@wecom/aibot-node-sdk" /app/extensions/wecom-openclaw-plugin/dist; then \
    if [ ! -d /app/extensions/wecom-openclaw-plugin/node_modules/@wecom/aibot-node-sdk ]; then \
      npm install --prefix /app/extensions/wecom-openclaw-plugin --no-save @wecom/aibot-node-sdk; \
    fi; \
  fi; \
  chown -R node:node /app/extensions/wecom-openclaw-plugin; \
  rm -rf "$tmp_home" "$tmp_pack"
ARG OPENCLAW_BUNDLED_EXTRA_PLUGIN_IDS=wecom-openclaw-plugin
RUN node <<'EOF'
const fs = require('fs');
const path = require('path');
const { createRequire } = require('module');
const builtInChannelRoot = '/app/extensions';
const manifestPath = path.join(builtInChannelRoot, '.openclaw-builtins.json');
const validPluginId = (value) => /^[a-z0-9._-]+$/.test(value);
const bundledExtraPluginIds = new Set(
  (process.env.OPENCLAW_BUNDLED_EXTRA_PLUGIN_IDS || '')
    .split(',')
    .map((value) => value.trim())
    .filter(validPluginId)
);
const readPluginPackageJson = (pluginDir) => {
  const packageJsonPath = path.join(pluginDir, 'package.json');
  if (!fs.existsSync(packageJsonPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(packageJsonPath, 'utf8'));
  } catch {
    return null;
  }
};
const resolveDeclaredChannelId = (packageJson) => {
  const openclaw = packageJson && typeof packageJson === 'object' ? packageJson.openclaw : null;
  const channel = openclaw && typeof openclaw === 'object' ? openclaw.channel : null;
  return channel && validPluginId(channel.id) ? channel.id : null;
};
const resolvePluginEntrypoint = (pluginDir, packageJson) => {
  const openclaw = packageJson && typeof packageJson === 'object' ? packageJson.openclaw : null;
  const extensions = openclaw && Array.isArray(openclaw.extensions) ? openclaw.extensions : [];
  const configuredEntrypoint = extensions.find((candidate) => typeof candidate === 'string' && candidate.trim());
  const candidates = [
    configuredEntrypoint ? path.resolve(pluginDir, configuredEntrypoint) : null,
    path.join(pluginDir, 'index.ts'),
    path.join(pluginDir, 'index.js'),
    path.join(pluginDir, 'src', 'index.ts'),
    path.join(pluginDir, 'src', 'index.js'),
    path.join(pluginDir, 'src', 'channel.ts'),
    path.join(pluginDir, 'src', 'channel.js'),
    path.join(pluginDir, 'channel.ts'),
    path.join(pluginDir, 'channel.js'),
  ];
  return candidates.find((candidate) => typeof candidate === 'string' && fs.existsSync(candidate)) || null;
};
const isBuiltInChannelPlugin = (pluginDir, packageJson) => {
  if (resolveDeclaredChannelId(packageJson)) return true;
  const description = (packageJson && typeof packageJson.description === 'string') ? packageJson.description.toLowerCase() : '';
  const entrypoint = resolvePluginEntrypoint(pluginDir, packageJson);
  if (!entrypoint) return description.includes('channel plugin');
  try {
    const source = fs.readFileSync(entrypoint, 'utf8');
    return source.includes('registerChannel(') || description.includes('channel plugin');
  } catch {
    return description.includes('channel plugin');
  }
};
const canResolveDependency = (pluginDir, dependency) => {
  if (!dependency || typeof dependency !== 'string') return false;
  try {
    const pluginRequire = createRequire(path.join(pluginDir, 'package.json'));
    pluginRequire.resolve(dependency);
    return true;
  } catch {
    try {
      require.resolve(dependency, { paths: [pluginDir, '/app'] });
      return true;
    } catch {
      return false;
    }
  }
};
const builtInChannels = [];
const bundledExtraPlugins = [];
for (const entry of fs.readdirSync(builtInChannelRoot, { withFileTypes: true })) {
  if (!entry.isDirectory() || !validPluginId(entry.name)) continue;
  const pluginId = entry.name;
  const channelDir = path.join(builtInChannelRoot, pluginId);
  const packageJson = readPluginPackageJson(channelDir);
  const dependencies = packageJson && typeof packageJson === 'object' ? Object.keys(packageJson.dependencies || {}) : [];
  const channelId = resolveDeclaredChannelId(packageJson) || pluginId;
  const loadable = dependencies.every((dependency) => canResolveDependency(channelDir, dependency));
  if (bundledExtraPluginIds.has(pluginId)) {
    bundledExtraPlugins.push({ pluginId, channelId, loadable });
    continue;
  }
  if (!isBuiltInChannelPlugin(channelDir, packageJson)) continue;
  builtInChannels.push({ channelId, loadable });
}
fs.writeFileSync(manifestPath, JSON.stringify({ channels: builtInChannels, builtInChannels, bundledExtraPlugins }, null, 2) + '\n');
EOF
RUN chown node:node /app/extensions/.openclaw-builtins.json

USER node
ENTRYPOINT ["docker-entrypoint.sh"]
