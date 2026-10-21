#!/usr/bin/env node
/**
 * 按 scripts/wheels.lock.json 下载纯 Python wheel 到 dist/engine/wheels/，用 node:crypto 校验 sha256。
 *
 * - 校验失败 → exit 1（不信任任何未锁定的字节）
 * - 复用 node_modules/.cache/grider-wheels/ 中已校验通过的副本，避免重复下载
 * - wheel 文件永不提交（.gitignore 覆盖 dist/ 与 wheels/）
 *
 * 用法：node scripts/fetch-wheels.mjs [--out <dir>]
 */
import { createHash } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(SCRIPT_DIR, '..');
const WHEELS_LOCK_PATH = path.join(SCRIPT_DIR, 'wheels.lock.json');
const CACHE_DIR = path.join(FRONTEND_DIR, 'node_modules', '.cache', 'grider-wheels');
const DOWNLOAD_TIMEOUT_MS = 60_000;

/**
 * 解析 --out 参数（默认 dist/engine）；wheel 落在 <out>/wheels/。
 * @param {string[]} argv
 * @returns {string}
 */
function parseOutDir(argv) {
  const idx = argv.indexOf('--out');
  if (idx === -1) return path.join(FRONTEND_DIR, 'dist', 'engine');
  const value = argv[idx + 1];
  if (!value) {
    console.error('[fetch-wheels] --out 需要一个目录参数');
    process.exit(2);
  }
  return path.resolve(value);
}

/**
 * @param {Uint8Array} bytes
 * @returns {string}
 */
function sha256Hex(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

/**
 * 读取缓存副本；sha256 不匹配时视为不存在。
 * @param {{filename: string, sha256: string}} wheel
 * @returns {Uint8Array|null}
 */
function readValidCache(wheel) {
  const cached = path.join(CACHE_DIR, wheel.filename);
  if (!existsSync(cached)) return null;
  const bytes = readFileSync(cached);
  return sha256Hex(bytes) === wheel.sha256 ? bytes : null;
}

/**
 * 下载并校验一个 wheel。
 * @param {{filename: string, url: string, sha256: string}} wheel
 * @returns {Promise<Uint8Array>}
 */
async function download(wheel) {
  const response = await fetch(wheel.url, { signal: AbortSignal.timeout(DOWNLOAD_TIMEOUT_MS) });
  if (!response.ok) throw new Error(`HTTP ${response.status} ${wheel.url}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  const actual = sha256Hex(bytes);
  if (actual !== wheel.sha256) {
    throw new Error(`sha256 不匹配 ${wheel.filename}: 期望 ${wheel.sha256} 实际 ${actual}`);
  }
  return bytes;
}

async function main() {
  const outDir = path.join(parseOutDir(process.argv.slice(2)), 'wheels');
  const wheels = JSON.parse(readFileSync(WHEELS_LOCK_PATH, 'utf-8'));
  mkdirSync(outDir, { recursive: true });
  mkdirSync(CACHE_DIR, { recursive: true });

  for (const wheel of wheels) {
    let bytes = readValidCache(wheel);
    let origin = 'cache';
    if (bytes === null) {
      bytes = await download(wheel);
      writeFileSync(path.join(CACHE_DIR, wheel.filename), bytes);
      origin = 'download';
    }
    const target = path.join(outDir, wheel.filename);
    if (origin === 'cache') {
      copyFileSync(path.join(CACHE_DIR, wheel.filename), target);
    } else {
      writeFileSync(target, bytes);
    }
    console.error(`[fetch-wheels] ${wheel.filename} ${bytes.length} bytes (${origin}) sha256 OK`);
  }
}

main().catch((err) => {
  console.error(`[fetch-wheels] 失败: ${err.message || err}`);
  process.exit(1);
});
