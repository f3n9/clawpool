# Speech Skills Design

## Goal
Preinstall two speech skills into the OpenClaw user image so every new user container can immediately:
- transcribe incoming voice/audio content to text with `qwen3-asr-flash`
- synthesize text to speech with `qwen3-tts-flash`

Users should not need to manually install, copy, or enable anything.

## Scope
This design covers:
- build-time packaging of two system skills into the OpenClaw image
- default runtime loading of system skills plus user workspace skills
- environment-variable based configuration for DashScope credentials/endpoints
- intended invocation behavior for ASR and TTS
- tests for image packaging and runtime config defaults

This design does not yet implement channel-specific auto-routing logic beyond making the skills available for agent use.

## Requirements
1. Two separate skills are provided:
   - `asr-transcribe`
   - `tts-synthesize`
2. Both skills are preinstalled in the image during Docker build.
3. Both skills are available by default in every new user container.
4. No API key is committed into the repository.
5. ASR must support both local audio files and public audio URLs.
6. TTS must only be used when the user explicitly asks for audio output.

## Architecture

### 1. Skill packaging
System skills will be stored in the image under:
- `/app/skills/asr-transcribe`
- `/app/skills/tts-synthesize`

Each skill directory will contain:
- `SKILL.md`
- small helper scripts used by the skill to call DashScope APIs

The skill body will describe:
- when to invoke the skill
- accepted inputs
- how outputs are returned
- constraints and failure behavior

### 2. Runtime skill loading
Today, new user runtimes default `skills.load.extraDirs` to only the persisted workspace path:
- `~/.openclaw/workspace/skills`

This will be expanded to include both:
- `/app/skills`
- `~/.openclaw/workspace/skills`

That preserves user-created skills while also making image-bundled skills automatically available.

### 3. ASR integration
The `asr-transcribe` skill will use DashScope native ASR with:
- model: `qwen3-asr-flash`

Input forms supported by the skill:
- local audio file path
- public audio URL
- optionally base64-encoded audio if needed by helper logic

Skill behavior:
- validate that the input resolves to an audio source
- call DashScope ASR
- return plain text transcription
- surface useful errors for unsupported file types, missing files, or API failures

The skill should be appropriate for:
- channel voice messages that are saved locally
- user-uploaded audio files when the user asks to transcribe them

### 4. TTS integration
The `tts-synthesize` skill will use DashScope multimodal generation with:
- model: `qwen3-tts-flash`

Skill behavior:
- accept input text and optional voice/style parameters if supported later
- generate audio via DashScope
- write the resulting audio to a temporary file inside the user workspace/runtime
- return the output path and a short summary

The skill must not be used unless the user explicitly asks for spoken output, such as:
- “convert this to speech”
- “read this aloud”
- “reply with audio”

### 5. Secrets and configuration
No secret values will be committed.

Runtime configuration will come from environment variables. Recommended variables:
- `OPENCLAW_DASHSCOPE_API_KEY`
- `OPENCLAW_DASHSCOPE_ASR_API_KEY` (optional override)
- `OPENCLAW_DASHSCOPE_TTS_API_KEY` (optional override)
- `OPENCLAW_DASHSCOPE_ASR_BASE_URL`
- `OPENCLAW_DASHSCOPE_TTS_BASE_URL`

Resolution order:
- ASR key uses `OPENCLAW_DASHSCOPE_ASR_API_KEY`, else falls back to `OPENCLAW_DASHSCOPE_API_KEY`
- TTS key uses `OPENCLAW_DASHSCOPE_TTS_API_KEY`, else falls back to `OPENCLAW_DASHSCOPE_API_KEY`

Default endpoints:
- ASR base URL defaults to the DashScope native ASR endpoint configured for `qwen3-asr-flash`
- TTS base URL defaults to the multimodal generation endpoint already specified by the user

### 6. Output and persistence
The skills themselves are image-bundled and read-only.
Generated artifacts should be written into user-persistent space so they survive container restarts if the user wants to keep them.

Recommended output locations:
- ASR temp/cache output: `~/.openclaw/workspace/data/audio/`
- TTS generated audio: `~/.openclaw/workspace/data/audio/`

### 7. Error handling
ASR skill should fail clearly when:
- no audio source is provided
- the local file does not exist
- the audio format is unsupported
- the DashScope request fails or times out

TTS skill should fail clearly when:
- no text is provided
- the text is too large for the chosen request mode
- the DashScope request fails or returns no audio payload

Both skills should avoid leaking secrets in logs or output.

## Files Expected To Change
- `infra/docker-build/Dockerfile`
- `services/instance-manager/services_instance_manager/main.py`
- `services/instance-manager/tests/jit_provision_test.py`
- `infra/tests/...` if image build validation needs coverage
- new skill directories under an image-bundled source path in the repo

## Testing Strategy
1. Unit test runtime config defaults so `skills.load.extraDirs` includes both system and workspace skill directories.
2. Validate the Docker build context installs/copies the two skill directories into the image.
3. Verify the image contains:
   - `/app/skills/asr-transcribe/SKILL.md`
   - `/app/skills/tts-synthesize/SKILL.md`
4. Verify no secret values are hardcoded.
5. Add smoke checks for helper scripts referencing the expected model names and env vars.

## Rollout
1. Merge image and instance-manager changes.
2. Build a new OpenClaw image tag.
3. Update default image/tag for future user containers.
4. Restart `instance-manager`.
5. Optionally recreate existing user containers only if you want them to pick up image-bundled system skills immediately.
