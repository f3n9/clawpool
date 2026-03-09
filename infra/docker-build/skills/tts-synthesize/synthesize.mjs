#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const DEFAULT_BASE_URL = 'https://dashscope-yxai.hatch.yinxiang.com/api/v1/services/aigc/multimodal-generation/generation';
const DEFAULT_MODEL = 'qwen3-tts-flash';
const DEFAULT_FORMAT = 'wav';
const DEFAULT_LANGUAGE_TYPE = 'Chinese';
const DEFAULT_VOICE = 'Cherry';

function parseArgs(argv) {
  const out = { text: '', textFile: '', output: '', voice: '', json: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--text') out.text = argv[++i] || '';
    else if (arg === '--text-file') out.textFile = argv[++i] || '';
    else if (arg === '--output') out.output = argv[++i] || '';
    else if (arg === '--voice') out.voice = argv[++i] || '';
    else if (arg === '--json') out.json = true;
  }
  return out;
}

function usage() {
  console.error('Usage: node synthesize.mjs (--text <text> | --text-file <path>) [--output <path>] [--voice <voice>] [--json]');
}

function resolveApiKey() {
  const apiKey = (process.env.OPENCLAW_DASHSCOPE_TTS_API_KEY || process.env.OPENCLAW_DASHSCOPE_API_KEY || '').trim();
  if (!apiKey) {
    throw new Error('missing OPENCLAW_DASHSCOPE_TTS_API_KEY or OPENCLAW_DASHSCOPE_API_KEY');
  }
  return apiKey;
}

function resolveText(args) {
  if (args.text && args.text.trim()) {
    return args.text.trim();
  }
  if (args.textFile) {
    return fs.readFileSync(path.resolve(args.textFile), 'utf8').trim();
  }
  throw new Error('missing text input');
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function defaultOutputPath() {
  const root = process.env.OPENCLAW_AUDIO_OUTPUT_DIR
    || path.join(process.env.HOME || '/home/node', '.openclaw', 'workspace', 'data', 'audio');
  ensureDir(root);
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  return path.join(root, `tts-${stamp}.${DEFAULT_FORMAT}`);
}

function pickAudioUrl(node) {
  if (!node || typeof node !== 'object') {
    return '';
  }
  const direct = [
    node.audio_url,
    node.audioUrl,
    node.url,
    node.output_audio_url,
    node.outputAudioUrl,
  ].find((value) => typeof value === 'string' && value.trim());
  if (direct) {
    return direct.trim();
  }
  if (Array.isArray(node)) {
    for (const item of node) {
      const found = pickAudioUrl(item);
      if (found) return found;
    }
    return '';
  }
  for (const value of Object.values(node)) {
    const found = pickAudioUrl(value);
    if (found) return found;
  }
  return '';
}

function pickAudioBase64(node) {
  if (!node || typeof node !== 'object') {
    return '';
  }
  const direct = [
    node.audio_base64,
    node.audioBase64,
    node.base64,
    node.data,
  ].find((value) => typeof value === 'string' && value.trim());
  if (direct) {
    return direct.trim();
  }
  if (Array.isArray(node)) {
    for (const item of node) {
      const found = pickAudioBase64(item);
      if (found) return found;
    }
    return '';
  }
  for (const value of Object.values(node)) {
    const found = pickAudioBase64(value);
    if (found) return found;
  }
  return '';
}

async function writeAudioFile(data, outputPath) {
  const url = pickAudioUrl(data.output || data);
  if (url) {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`failed to download synthesized audio (${response.status}) from ${url}`);
    }
    const buffer = Buffer.from(await response.arrayBuffer());
    fs.writeFileSync(outputPath, buffer);
    return { outputPath, source: 'url', url };
  }

  const base64 = pickAudioBase64(data.output || data);
  if (base64) {
    fs.writeFileSync(outputPath, Buffer.from(base64, 'base64'));
    return { outputPath, source: 'base64' };
  }

  throw new Error(`TTS response did not contain downloadable audio: ${JSON.stringify(data)}`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const text = resolveText(args);
  if (!text) {
    usage();
    process.exit(2);
  }

  const apiKey = resolveApiKey();
  const baseUrl = (process.env.OPENCLAW_DASHSCOPE_TTS_BASE_URL || DEFAULT_BASE_URL).trim();
  const model = (process.env.OPENCLAW_DASHSCOPE_TTS_MODEL || DEFAULT_MODEL).trim();
  const voice = (args.voice || process.env.OPENCLAW_DASHSCOPE_TTS_VOICE || DEFAULT_VOICE).trim();
  const languageType = (process.env.OPENCLAW_DASHSCOPE_TTS_LANGUAGE_TYPE || DEFAULT_LANGUAGE_TYPE).trim();
  const outputPath = path.resolve(args.output || defaultOutputPath());
  ensureDir(path.dirname(outputPath));

  const payload = {
    model,
    input: {
      text,
      voice,
      language_type: languageType,
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
    throw new Error(`non-JSON TTS response (${response.status}): ${raw.slice(0, 400)}`);
  }
  if (!response.ok) {
    throw new Error(`TTS request failed (${response.status}): ${JSON.stringify(data)}`);
  }

  const written = await writeAudioFile(data, outputPath);
  if (args.json) {
    console.log(JSON.stringify({ outputPath: written.outputPath, source: written.source, response: data }, null, 2));
    return;
  }
  console.log(written.outputPath);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
