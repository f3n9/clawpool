#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';

const DEFAULT_BASE_URL = 'https://dashscope-yxai.hatch.yinxiang.com/api/v1/services/aigc/multimodal-generation/generation';
const DEFAULT_MODEL = 'qwen-image-2.0';
const DEFAULT_EXTENSION = 'png';

function parseArgs(argv) {
  const out = { prompt: '', output: '', json: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--prompt') out.prompt = argv[++i] || '';
    else if (arg === '--output') out.output = argv[++i] || '';
    else if (arg === '--json') out.json = true;
  }
  return out;
}

function usage() {
  console.error('Usage: node generate.mjs --prompt "describe the image" [--output /path/to/file.png] [--json]');
}

function resolveApiKey() {
  const apiKey = (process.env.OPENCLAW_DASHSCOPE_IMAGE_API_KEY || process.env.OPENCLAW_DASHSCOPE_API_KEY || '').trim();
  if (!apiKey) {
    throw new Error('missing OPENCLAW_DASHSCOPE_IMAGE_API_KEY or OPENCLAW_DASHSCOPE_API_KEY');
  }
  return apiKey;
}

function ensureDir(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

function defaultOutputRoot() {
  return process.env.OPENCLAW_IMAGE_OUTPUT_DIR
    || path.join(process.env.HOME || '/home/node', '.openclaw', 'workspace', 'data', 'images');
}

function defaultOutputPath() {
  const root = defaultOutputRoot();
  ensureDir(root);
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  return path.join(root, `image-${stamp}.${DEFAULT_EXTENSION}`);
}

function guessExtensionFromType(value) {
  const normalized = String(value || '').toLowerCase();
  if (normalized.includes('image/jpeg') || normalized.includes('image/jpg')) return 'jpg';
  if (normalized.includes('image/webp')) return 'webp';
  if (normalized.includes('image/gif')) return 'gif';
  if (normalized.includes('image/svg')) return 'svg';
  return 'png';
}

function maybeSwapExtension(outputPath, extension) {
  const ext = (extension || '').replace(/^\./, '').trim();
  if (!ext) return outputPath;
  const parsed = path.parse(outputPath);
  return path.join(parsed.dir, `${parsed.name}.${ext}`);
}

function pickString(node, keys) {
  if (!node || typeof node !== 'object') {
    return '';
  }
  for (const key of keys) {
    const value = node[key];
    if (typeof value === 'string' && value.trim()) {
      return value.trim();
    }
  }
  if (Array.isArray(node)) {
    for (const item of node) {
      const found = pickString(item, keys);
      if (found) return found;
    }
    return '';
  }
  for (const value of Object.values(node)) {
    const found = pickString(value, keys);
    if (found) return found;
  }
  return '';
}

function pickImageUrl(node) {
  return pickString(node, [
    'url',
    'image_url',
    'imageUrl',
    'output_image_url',
    'outputImageUrl',
    'result_url',
    'resultUrl',
  ]);
}

function pickImageBase64(node) {
  return pickString(node, [
    'image_base64',
    'imageBase64',
    'base64',
    'image',
    'data',
  ]);
}

function pickMimeType(node) {
  return pickString(node, [
    'mime_type',
    'mimeType',
    'content_type',
    'contentType',
  ]);
}

function workspaceRelativePath(outputPath) {
  const workspaceRoot = path.join(process.env.HOME || '/home/node', '.openclaw', 'workspace');
  const absolutePath = path.resolve(outputPath);
  if (absolutePath === workspaceRoot || absolutePath.startsWith(`${workspaceRoot}${path.sep}`)) {
    return path.relative(workspaceRoot, absolutePath).split(path.sep).join('/');
  }
  return '';
}

function buildDownloadPath(relativePath) {
  if (!relativePath) {
    return '';
  }
  return `/files/${relativePath}`;
}

function stripDataUrlPrefix(value) {
  const trimmed = String(value || '').trim();
  const match = trimmed.match(/^data:([^;,]+)?;base64,(.+)$/i);
  if (!match) {
    return { mimeType: '', data: trimmed };
  }
  return { mimeType: match[1] || '', data: match[2] || '' };
}

async function writeImageFile(data, requestedPath) {
  const outputNode = data.output || data;
  const imageUrl = pickImageUrl(outputNode);
  if (imageUrl) {
    const response = await fetch(imageUrl);
    if (!response.ok) {
      throw new Error(`failed to download generated image (${response.status}) from ${imageUrl}`);
    }
    const contentType = response.headers.get('content-type') || '';
    const resolvedPath = maybeSwapExtension(requestedPath, guessExtensionFromType(contentType || imageUrl));
    ensureDir(path.dirname(resolvedPath));
    const buffer = Buffer.from(await response.arrayBuffer());
    fs.writeFileSync(resolvedPath, buffer);
    return { outputPath: resolvedPath, source: 'url', url: imageUrl };
  }

  const imageBase64Raw = pickImageBase64(outputNode);
  if (imageBase64Raw) {
    const parsed = stripDataUrlPrefix(imageBase64Raw);
    const mimeType = pickMimeType(outputNode) || parsed.mimeType;
    const resolvedPath = maybeSwapExtension(requestedPath, guessExtensionFromType(mimeType));
    ensureDir(path.dirname(resolvedPath));
    fs.writeFileSync(resolvedPath, Buffer.from(parsed.data, 'base64'));
    return { outputPath: resolvedPath, source: 'base64' };
  }

  throw new Error(`image response did not contain downloadable image data: ${JSON.stringify(data)}`);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const prompt = (args.prompt || '').trim();
  if (!prompt) {
    usage();
    process.exit(2);
  }

  const apiKey = resolveApiKey();
  const baseUrl = (process.env.OPENCLAW_DASHSCOPE_IMAGE_BASE_URL || DEFAULT_BASE_URL).trim();
  const model = (process.env.OPENCLAW_DASHSCOPE_IMAGE_MODEL || DEFAULT_MODEL).trim();
  const requestedPath = path.resolve(args.output || defaultOutputPath());
  ensureDir(path.dirname(requestedPath));

  const payload = {
    model,
    input: {
      messages: [
        {
          role: 'user',
          content: [
            {
              text: prompt,
            },
          ],
        },
      ],
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
    throw new Error(`non-JSON image response (${response.status}): ${raw.slice(0, 400)}`);
  }
  if (!response.ok) {
    throw new Error(`image request failed (${response.status}): ${JSON.stringify(data)}`);
  }

  const written = await writeImageFile(data, requestedPath);
  const relativePath = workspaceRelativePath(written.outputPath);
  const result = {
    outputPath: written.outputPath,
    relativePath,
    downloadPath: buildDownloadPath(relativePath),
    source: written.source,
    url: written.url || '',
  };

  if (args.json) {
    console.log(JSON.stringify({ ...result, response: data }, null, 2));
    return;
  }
  console.log(result.outputPath);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
});
