#!/usr/bin/env node
/**
 * Pages 构建收尾（design.md §7.3）：
 * - 复制 engine/pyodide.worker.js → dist/engine/pyodide.worker.js（classic worker 不经 Vite）
 * - 复制 dist/index.html → dist/404.html（GitHub Pages 深链接回退，SPA 自行路由）
 * - 写入 dist/.nojekyll
 * - 断言 dist/engine/{pyodide.worker.js, backend.zip, manifest.json, wheels/*.whl} 存在，
 *   wheel 文件名集合与 engineConfig.WHEEL_FILES 完全一致，且 dist/index.html 引用 /grider/assets/
 *   （防止漏掉 --mode pages）。任一断言失败 → exit 1。
 *
 * 用法：node scripts/postbuild-pages.mjs [--dist <dir>]
 */
import { copyFileSync, existsSync, readFileSync, readdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(SCRIPT_DIR, '..');
const WORKER_SRC = path.join(FRONTEND_DIR, 'engine', 'pyodide.worker.js');
const ENGINE_CONFIG_PATH = path.join(FRONTEND_DIR, 'src', 'shared', 'services', 'engine', 'engineConfig.js');
const PAGES_ASSET_PREFIX = '/grider/assets/';

/**
 * @param {string[]} argv
 * @returns {string}
 */
function parseDistDir(argv) {
  const idx = argv.indexOf('--dist');
  if (idx === -1) return path.join(FRONTEND_DIR, 'dist');
  const value = argv[idx + 1];
  if (!value) {
    console.error('[postbuild-pages] --dist 需要一个目录参数');
    process.exit(2);
  }
  return path.resolve(value);
}

/**
 * @param {boolean} condition
 * @param {string} message
 */
function assertOrExit(condition, message) {
  if (condition) return;
  console.error(`[postbuild-pages] 断言失败: ${message}`);
  process.exit(1);
}

async function main() {
  const distDir = parseDistDir(process.argv.slice(2));
  const engineDir = path.join(distDir, 'engine');
  const indexHtml = path.join(distDir, 'index.html');

  assertOrExit(existsSync(indexHtml), `${indexHtml} 不存在（先运行 vite build --mode pages）`);
  assertOrExit(existsSync(engineDir), `${engineDir} 不存在（先运行 npm run build:engine）`);
  assertOrExit(existsSync(WORKER_SRC), `${WORKER_SRC} 不存在`);

  copyFileSync(WORKER_SRC, path.join(engineDir, 'pyodide.worker.js'));
  copyFileSync(indexHtml, path.join(distDir, '404.html'));
  writeFileSync(path.join(distDir, '.nojekyll'), '');

  for (const name of ['pyodide.worker.js', 'backend.zip', 'manifest.json']) {
    assertOrExit(existsSync(path.join(engineDir, name)), `dist/engine/${name} 缺失`);
  }
  const wheelsDir = path.join(engineDir, 'wheels');
  assertOrExit(existsSync(wheelsDir), 'dist/engine/wheels 缺失');
  const wheels = readdirSync(wheelsDir).filter((f) => f.endsWith('.whl')).sort();
  assertOrExit(wheels.length > 0, 'dist/engine/wheels 中没有 .whl');
  const { WHEEL_FILES } = await import(pathToFileURL(ENGINE_CONFIG_PATH).href);
  const expected = [...WHEEL_FILES].sort();
  assertOrExit(
    JSON.stringify(wheels) === JSON.stringify(expected),
    `wheel 文件集合与 engineConfig.WHEEL_FILES 不一致：dist=${wheels.join(',')} 期望=${expected.join(',')}`,
  );

  const html = readFileSync(indexHtml, 'utf-8');
  assertOrExit(html.includes(PAGES_ASSET_PREFIX), `dist/index.html 未引用 ${PAGES_ASSET_PREFIX}（缺少 --mode pages？）`);

  console.error(`[postbuild-pages] OK: worker/404.html/.nojekyll 已写入，${wheels.length} 个 wheel，index.html 引用 ${PAGES_ASSET_PREFIX}`);
}

main().catch((err) => {
  console.error(`[postbuild-pages] 失败: ${err.stack || err}`);
  process.exit(1);
});
