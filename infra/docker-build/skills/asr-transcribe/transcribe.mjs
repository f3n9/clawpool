#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const DEFAULT_BASE_URL = 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions';
const LEGACY_MULTIMODAL_PATH = '/api/v1/services/aigc/multimodal-generation/generation';
const COMPATIBLE_CHAT_PATH = '/compatible-mode/v1/chat/completions';
const DEFAULT_MODEL = 'qwen3-asr-flash';

function parseArgs(argv) {
  const out = { input: '', json: false, prompt: 'Please transcribe this audio to plain text.' };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--input') out.input = argv[++i] || '';
    else if (arg === '--prompt') out.prompt = argv[++i] || out.prompt;
    else if (arg === '--json') out.json = true;
    else if (!out.input && !arg.startsWith('--')) out.input = arg;
  }
  return out;
}

function usage() {
  console.error('Usage: node transcribe.mjs --input <absolute-file-path-or-public-url> [--json]');
}

function isHttpUrl(value) {
  return /^https?:\/\//i.test(value || '');
}

function resolveAudioSource(input) {
  if (!input) {
    throw new Error('missing input');
  }
  if (isHttpUrl(input)) {
    return { kind: 'url', value: input };
  }
  const abs = path.resolve(input);
  if (!path.isAbsolute(abs) || !fs.existsSync(abs)) {
    throw new Error(`audio file not found: ${input}`);
  }
  return { kind: 'file', value: abs };
}

function resolveApiKey() {
  const apiKey = (process.env.OPENCLAW_DASHSCOPE_ASR_API_KEY || process.env.OPENCLAW_DASHSCOPE_API_KEY || '').trim();
  if (!apiKey) {
    throw new Error('missing OPENCLAW_DASHSCOPE_ASR_API_KEY or OPENCLAW_DASHSCOPE_API_KEY');
  }
  return apiKey;
}

function normalizeBaseUrl(value) {
  const candidate = (value || DEFAULT_BASE_URL).trim() || DEFAULT_BASE_URL;
  try {
    const url = new URL(candidate);
    if (url.pathname === LEGACY_MULTIMODAL_PATH) {
      url.pathname = COMPATIBLE_CHAT_PATH;
      url.search = '';
      url.hash = '';
      return url.toString();
    }
  } catch {
    return candidate;
  }
  return candidate;
}

function guessMimeType(filePath) {
  switch (path.extname(filePath).toLowerCase()) {
    case '.wav':
      return 'audio/wav';
    case '.mp3':
      return 'audio/mpeg';
    case '.m4a':
      return 'audio/mp4';
    case '.aac':
      return 'audio/aac';
    case '.ogg':
      return 'audio/ogg';
    case '.flac':
      return 'audio/flac';
    case '.webm':
      return 'audio/webm';
    default:
      return 'application/octet-stream';
  }
}

function buildAudioData(source) {
  if (source.kind === 'url') {
    return source.value;
  }
  const audioBase64 = fs.readFileSync(source.value).toString('base64');
  const mimeType = guessMimeType(source.value);
  return `data:${mimeType};base64,${audioBase64}`;
}

function buildPayload(model, audioData) {
  return {
    model,
    messages: [
      {
        role: 'user',
        content: [
          {
            type: 'input_audio',
            input_audio: { data: audioData },
          },
        ],
      },
    ],
    stream: false,
    asr_options: {
      enable_itn: false,
    },
  };
}

function extractText(node) {
  if (typeof node === 'string') {
    return node.trim();
  }
  if (!node || typeof node !== 'object') {
    return '';
  }
  if (Array.isArray(node.choices)) {
    for (const choice of node.choices) {
      const content = extractText(choice?.message?.content);
      if (content) {
        return content;
      }
    }
  }
  const directKeys = ['text', 'transcript', 'content', 'output_text'];
  for (const key of directKeys) {
    if (typeof node[key] === 'string' && node[key].trim()) {
      return node[key].trim();
    }
  }
  if (Array.isArray(node)) {
    return node.map((item) => extractText(item)).filter(Boolean).join('\n').trim();
  }
  for (const value of Object.values(node)) {
    const nested = extractText(value);
    if (nested) {
      return nested;
    }
  }
  return '';
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (!args.input) {
    usage();
    process.exit(2);
  }

  const apiKey = resolveApiKey();
  const baseUrl = normalizeBaseUrl(process.env.OPENCLAW_DASHSCOPE_ASR_BASE_URL || DEFAULT_BASE_URL);
  const model = (process.env.OPENCLAW_DASHSCOPE_ASR_MODEL || DEFAULT_MODEL).trim();
  const source = resolveAudioSource(args.input);
  const audioData = buildAudioData(source);
  const payload = buildPayload(model, audioData);

  const response = await fetch(baseUrl, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(payload),
  });

  const raw = await response.text();
  let data;
  try {
    data = JSON.parse(raw);
  } catch {
    throw new Error(`non-JSON ASR response (${response.status}): ${raw.slice(0, 400)}`);
  }
  if (!response.ok) {
    throw new Error(`ASR request failed (${response.status}): ${JSON.stringify(data)}`);
  }

  const text = extractText(data);
  if (!text) {
    throw new Error(`ASR response did not contain transcript text: ${JSON.stringify(data)}`);
  }

  if (args.json) {
    console.log(JSON.stringify({ text, response: data }, null, 2));
    return;
  }
  console.log(text);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
