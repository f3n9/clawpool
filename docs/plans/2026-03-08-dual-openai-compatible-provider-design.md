# Dual OpenAI-Compatible Provider Design

**Goal:** Keep the existing OpenAI provider and models, add a second OpenAI-compatible provider for DashScope-hosted models, and make `dashscope/MiniMax-M2.5` the default agent model.

## Scope
- Preserve existing `openai` provider config and bundled GPT models.
- Add a second provider named `dashscope`.
- Configure `dashscope` to use the supplied chat/completions-compatible endpoint and API key.
- Register the following models under `dashscope`:
  - `MiniMax-M2.5`
  - `kimi-k2.5`
  - `deepseek-v3.2`
  - `qwen3.5-flash`
- Change the default model selection to `dashscope/MiniMax-M2.5`.

## Approach
1. Keep the current OpenAI provider-generation logic for `openai/*` references.
2. Add a second provider-generation path for `dashscope/*` references.
3. Teach default-model and allowed-model normalization to preserve explicit provider prefixes instead of forcing everything into `openai/*`.
4. Seed the DashScope provider from env defaults when the runtime config is first generated or reconciled.

## Defaults
- Existing OpenAI provider remains:
  - endpoint from existing env/defaults
  - existing GPT models remain available
- New DashScope provider defaults:
  - `baseUrl`: `https://dashscope-yxai.hatch.yinxiang.com/compatible-mode/v1/chat/completions`
  - `api`: `openai-completions`
  - API key: `OPENCLAW_DASHSCOPE_API_KEY`
  - models: `MiniMax-M2.5`, `kimi-k2.5`, `deepseek-v3.2`, `qwen3.5-flash`
- New default agent model:
  - `dashscope/MiniMax-M2.5`

## Safety Notes
- The change is additive for provider definitions.
- Existing `openai/*` model refs stay valid.
- Only the default primary model changes.
- SSE transport will still be enforced for both providers in agent model params.
