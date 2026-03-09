---
name: tts-synthesize
description: Use when a user explicitly asks to convert text into spoken audio, read text aloud, or reply with voice output.
---

# TTS Synthesize

## Overview
Generate speech audio with DashScope TTS using `qwen3-tts-flash`.

## When to Use
- User explicitly asks to convert text to audio.
- User asks for a spoken reply or for text to be read aloud.

Do not use this skill unless the user clearly wants audio output.

## Inputs
- Text to synthesize
- Optional output path if the caller wants a specific filename

## Configuration
- `OPENCLAW_DASHSCOPE_TTS_API_KEY`
- `OPENCLAW_DASHSCOPE_API_KEY` fallback
- `OPENCLAW_DASHSCOPE_TTS_BASE_URL` optional override
- `OPENCLAW_DASHSCOPE_TTS_MODEL` optional override, defaults to `qwen3-tts-flash`
- `OPENCLAW_DASHSCOPE_TTS_VOICE` optional default voice

## Use
1. Collect the exact text that should be spoken.
2. Run:
   - `node synthesize.mjs --text "Hello world"`
   - or `node synthesize.mjs --text-file /path/to/text.txt`
3. Return the generated audio file path.
4. Send or reference that audio file in the channel reply.

## Notes
- Keep TTS opt-in only.
- The helper saves audio into persistent workspace-backed storage unless an explicit output path is supplied.
