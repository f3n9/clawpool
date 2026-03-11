---
name: image-generate
description: Use when a user explicitly asks to generate, draw, create, or illustrate an image from a text prompt.
---

# Image Generate

## Overview
Generate an image with DashScope using `qwen-image-2.0`.

## When to Use
- The user explicitly asks for a new image, picture, illustration, drawing, poster, or similar visual output.
- The user provides a prompt describing what the image should contain.

Do not use this skill for ordinary text replies or for image analysis.

## Configuration
- `OPENCLAW_DASHSCOPE_IMAGE_API_KEY`
- `OPENCLAW_DASHSCOPE_API_KEY` fallback
- `OPENCLAW_DASHSCOPE_IMAGE_BASE_URL` optional override
- `OPENCLAW_DASHSCOPE_IMAGE_MODEL` optional override, defaults to `qwen-image-2.0`
- `OPENCLAW_IMAGE_OUTPUT_DIR` optional override, defaults to `~/.openclaw/workspace/data/images`

## Use
1. Confirm the exact image prompt if the request is underspecified.
2. Run:
   - `node generate.mjs --prompt "a watercolor mountain village at sunrise" --json`
3. Read `downloadUrl` from the JSON result when it is present. Only fall back to `downloadPath` or `outputPath` if `downloadUrl` is empty.
4. If the current channel supports image attachments, attach the saved image in the reply.
5. If attachment sending is unavailable, prefer returning the public download URL such as `https://claw.hatch.yinxiang.com/files/data/images/example.png`. Only return the saved local path when no public `/files/...` URL can be constructed.

## Notes
- Keep this skill opt-in only; use it only when the user explicitly asks for image generation.
- The helper saves files into persistent workspace-backed storage so they survive container restarts.
- Keep the reply focused on the generated image and the download URL.
