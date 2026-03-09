# WeCom Plugin Replacement Design

**Goal:** Replace the image-bundled WeCom plugin package with `@wecom/wecom-openclaw-plugin` while preserving the existing runtime behavior that forces the `wecom` plugin/channel on by default.

## Approach
- Update the image build step to install `@wecom/wecom-openclaw-plugin`.
- Normalize the installed extension directory back to the stable plugin id `wecom` during image build, so the existing runtime reconciliation and default-enable logic continue to work unchanged.
- Keep `/opt/openclaw/extensions/wecom` as the bundled plugin location copied into user runtimes.

## Risk Control
- Do not assume the installed directory name matches the plugin id.
- Detect the installed extension directory dynamically and rename/copy it to `wecom`.
- Verify by rebuilding the image and checking the bundled extension path and runtime config behavior in a fresh container.
