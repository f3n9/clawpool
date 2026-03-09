#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const DEFAULT_BASE_URL = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation';
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
  console.error('Usage: node transcribe.mjs --input <absolute-file-path-or-public-url> [--prompt <text>] [--json]');
}

function isHttpUrl(value) {
  return /^https?:\/\//i.test(value || '');
}

function resolveAudioSource(input) {
  if (!input) {
    throw new Error('missing input');
  }
  if (isHttpUrl(input)) {
    return input;
  }
  const abs = path.resolve(input);
  if (!path.isAbsolute(abs) || !fs.existsSync(abs)) {
    throw new Error(`audio file not found: ${input}`);
  }
  return `file://${abs}`;
}

function resolveApiKey() {
  const apiKey = (process.env.OPENCLAW_DASHSCOPE_ASR_API_KEY || process.env.OPENCLAW_DASHSCOPE_API_KEY || '').trim();
  if (!apiKey) {
    throw new Error('missing OPENCLAW_DASHSCOPE_ASR_API_KEY or OPENCLAW_DASHSCOPE_API_KEY');
  }
  return apiKey;
}

function extractText(node) {
  if (typeof node === 'string') {
    return node.trim();
  }
  if (!node || typeof node !== 'object') {
    return '';
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
  const baseUrl = (process.env.OPENCLAW_DASHSCOPE_ASR_BASE_URL || DEFAULT_BASE_URL).trim();
  const model = (process.env.OPENCLAW_DASHSCOPE_ASR_MODEL || DEFAULT_MODEL).trim();
  const audio = resolveAudioSource(args.input);

  const payload = {
    model,
    input: {
      messages: [
        {
          role: 'user',
          content: [
            { audio },
            { text: args.prompt },
          ],
        },
      ],
    },
    parameters: {
      result_format: 'message',
    },
  };

  const response = await fetch(baseUrl, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
      'X-DashScope-SSE': 'disable',
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

  const text = extractText(data.output || data);
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
