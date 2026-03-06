#!/bin/sh
set -eu

BUNDLED_EXTENSIONS_DIR="/opt/openclaw/extensions"
RUNTIME_EXTENSIONS_DIR="${HOME:-/home/node}/.openclaw/extensions"

if [ -d "${BUNDLED_EXTENSIONS_DIR}" ]; then
  mkdir -p "${RUNTIME_EXTENSIONS_DIR}"
  for plugin_dir in "${BUNDLED_EXTENSIONS_DIR}"/*; do
    [ -d "${plugin_dir}" ] || continue
    plugin_name="$(basename "${plugin_dir}")"
    target_dir="${RUNTIME_EXTENSIONS_DIR}/${plugin_name}"
    if [ ! -d "${target_dir}" ]; then
      cp -a "${plugin_dir}" "${target_dir}"
    fi
  done
fi

exec docker-entrypoint.sh "$@"
