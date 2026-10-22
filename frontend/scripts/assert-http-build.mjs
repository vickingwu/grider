#!/usr/bin/env node
/**
 * http 构建不变量（design.md §6.1）：在 `npm run build` 之后运行，断言
 * - dist/engine 不存在
 * - dist/assets/*.js 不含 /pyodide/i
 * - dist/index.html 不含 '/grider/'
 * 任一失败 → exit 1。committed static/ 永不重建，本脚本只检查 dist/。
 *
 * 用法：node scripts/assert-http-build.mjs [--dist <dir>]
 */
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(SCRIPT_DIR, '..');
const PYODIDE_PATTERN = /pyodide/i;
const PAGES_BASE = '/grider/';

/**
 * @param {string[]} argv
 * @returns {string}
 */
function parseDistDir(argv) {
  const idx = argv.indexOf('--dist');
  if (idx === -1) return path.join(FRONTEND_DIR, 'dist');
  const value = argv[idx + 1];
  if (!value) {
    console.error('[assert-http-build] --dist 需要一个目录参数');
    process.exit(2);
  }
  return path.resolve(value);
}

function main() {
  const distDir = parseDistDir(process.argv.slice(2));
  const failures = [];

  if (!existsSync(path.join(distDir, 'index.html'))) failures.push('dist/index.html 不存在（先运行 npm run build）');
  if (existsSync(path.join(distDir, 'engine'))) failures.push('dist/engine 不应存在于 http 构建');

  const assetsDir = path.join(distDir, 'assets');
  const jsFiles = existsSync(assetsDir) ? readdirSync(assetsDir).filter((f) => f.endsWith('.js')) : [];
  if (!jsFiles.length) failures.push('dist/assets 中没有 .js');
  for (const file of jsFiles) {
    if (PYODIDE_PATTERN.test(readFileSync(path.join(assetsDir, file), 'utf-8'))) failures.push(`dist/assets/${file} 含 "pyodide" 子串`);
  }

  if (existsSync(path.join(distDir, 'index.html')) && readFileSync(path.join(distDir, 'index.html'), 'utf-8').includes(PAGES_BASE)) {
    failures.push(`dist/index.html 含 ${PAGES_BASE}`);
  }

  if (failures.length) {
    console.error('[assert-http-build] FAIL');
    for (const f of failures) console.error(`  - ${f}`);
    process.exit(1);
  }
  console.error(`[assert-http-build] OK: 无 dist/engine，${jsFiles.length} 个 JS 无 pyodide 子串，index.html 无 ${PAGES_BASE}`);
}

main();
