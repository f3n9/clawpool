---
name: asr-transcribe
description: Use when a user sends a voice message, uploads an audio file, or provides a public audio URL and wants a text transcription.
---

# ASR Transcribe

## Overview
Transcribe speech to text with DashScope native ASR using `qwen3-asr-flash`.

## When to Use
- User sends a voice message and wants the spoken content as text.
- User uploads an audio file and explicitly asks for transcription.
- User provides a public audio URL and asks for transcription.

Do not use this skill for TTS or for ordinary text-only requests.

## Inputs
- Absolute local audio file path, or
- Public `http://` or `https://` audio URL

## Configuration
- `OPENCLAW_DASHSCOPE_ASR_API_KEY`
- `OPENCLAW_DASHSCOPE_API_KEY` fallback
- `OPENCLAW_DASHSCOPE_ASR_BASE_URL` optional override
- `OPENCLAW_DASHSCOPE_ASR_MODEL` optional override, defaults to `qwen3-asr-flash`

## Use
1. Resolve one audio source.
2. Run:
   - `node transcribe.mjs --input /absolute/path/to/audio.wav`
   - or `node transcribe.mjs --input https://example.com/audio.mp3`
3. Return the transcript text to the user.
4. If the helper prints JSON with `--json`, extract the `text` field unless the user asked for metadata.

## Notes
- Prefer the saved local file path for channel voice messages.
- Keep the reply focused on the transcript unless the user asks for timestamps or raw metadata.
