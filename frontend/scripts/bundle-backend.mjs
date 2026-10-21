#!/usr/bin/env node
/**
 * 把 ../backend/app 打包为 dist/engine/backend.zip（条目位于 app/… 之下），并写出 dist/engine/manifest.json。
 *
 * 规则（design.md §7.1）：
 * - 只收录 *.py（天然排除杂散文件 constants.py''）
 * - 排除目录：external/、__pycache__/、config/、middleware/、models/、任何名为 tests 的目录
 * - 排除文件：services/demo_service.py（模块级 from app import db）
 * - 列 0 禁止导入检查：/^(from|import)\s+app\.(middleware|models)\b/m → 命中则 exit 1 并打印 file:line
 * - app/browser/bootstrap.py 与 app/services/providers/browser_market_data.py 缺失 → exit 1
 *
 * 用法：node scripts/bundle-backend.mjs [--out <dir>]
 */
import { execSync } from 'node:child_process';
import { mkdirSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { zipSync } from 'fflate';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(SCRIPT_DIR, '..');
const APP_DIR = path.resolve(FRONTEND_DIR, '..', 'backend', 'app');
const ENGINE_CONFIG_PATH = path.join(FRONTEND_DIR, 'src', 'shared', 'services', 'engine', 'engineConfig.js');
const WHEELS_LOCK_PATH = path.join(SCRIPT_DIR, 'wheels.lock.json');

const EXCLUDED_DIRS = new Set(['external', '__pycache__', 'config', 'middleware', 'models', 'tests']);
const EXCLUDED_FILES = new Set(['services/demo_service.py']);
const REQUIRED_FILES = ['app/browser/bootstrap.py', 'app/services/providers/browser_market_data.py'];
const FORBIDDEN_IMPORT = /^(from|import)\s+app\.(middleware|models)\b/m;
const ZIP_LEVEL = 6;

/**
 * 解析 --out 参数。
 * @param {string[]} argv
 * @returns {string}
 */
function parseOutDir(argv) {
  const idx = argv.indexOf('--out');
  if (idx === -1) return path.join(FRONTEND_DIR, 'dist', 'engine');
  const value = argv[idx + 1];
  if (!value) {
    console.error('[bundle-backend] --out 需要一个目录参数');
    process.exit(2);
  }
  return path.resolve(value);
}

/**
 * 递归收集符合规则的 .py 文件（相对 app/ 的 POSIX 路径）。
 * @param {string} dir
 * @param {string} rel
 * @param {string[]} acc
 * @returns {string[]}
 */
function collectPythonFiles(dir, rel, acc) {
  const entries = readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name));
  for (const entry of entries) {
    const relPath = rel ? `${rel}/${entry.name}` : entry.name;
    if (entry.isDirectory()) {
      if (EXCLUDED_DIRS.has(entry.name)) continue;
      collectPythonFiles(path.join(dir, entry.name), relPath, acc);
      continue;
    }
    if (!entry.isFile() || !/\.py$/.test(entry.name)) continue;
    if (EXCLUDED_FILES.has(relPath)) continue;
    acc.push(relPath);
  }
  return acc;
}

/**
 * 列 0 禁止导入检查；返回 "file:line" 列表。
 * @param {string} relPath
 * @param {string} source
 * @returns {string[]}
 */
function findForbiddenImports(relPath, source) {
  const hits = [];
  const lines = source.split(/\r?\n/);
  lines.forEach((line, i) => {
    if (FORBIDDEN_IMPORT.test(line)) hits.push(`app/${relPath}:${i + 1}`);
  });
  return hits;
}

/**
 * 读取 git 短 SHA（CI 优先 GITHUB_SHA）。
 * @returns {string}
 */
function resolveGitSha() {
  if (process.env.GITHUB_SHA) return process.env.GITHUB_SHA.slice(0, 7);
  try {
    return execSync('git rev-parse --short HEAD', { cwd: FRONTEND_DIR, encoding: 'utf-8', windowsHide: true }).trim();
  } catch {
    return 'unknown';
  }
}

async function main() {
  const outDir = parseOutDir(process.argv.slice(2));
  statSync(APP_DIR);

  const relFiles = collectPythonFiles(APP_DIR, '', []);
  const zipInput = {};
  const violations = [];
  for (const rel of relFiles) {
    const source = readFileSync(path.join(APP_DIR, rel));
    violations.push(...findForbiddenImports(rel, source.toString('utf-8')));
    zipInput[`app/${rel}`] = [new Uint8Array(source), { level: ZIP_LEVEL }];
  }
  if (violations.length) {
    console.error('[bundle-backend] 发现列 0 的 app.middleware/app.models 导入（浏览器 zip 不含这些包）：');
    for (const v of violations) console.error(`  ${v}`);
    process.exit(1);
  }
  const entryNames = Object.keys(zipInput);
  const missing = REQUIRED_FILES.filter((f) => !entryNames.includes(f));
  if (missing.length) {
    console.error(`[bundle-backend] 缺少必需文件: ${missing.join(', ')}`);
    process.exit(1);
  }

  const zipBytes = zipSync(zipInput);
  const { PYODIDE_VERSION } = await import(pathToFileURL(ENGINE_CONFIG_PATH).href);
  const wheels = JSON.parse(readFileSync(WHEELS_LOCK_PATH, 'utf-8')).map(({ name, version, filename, sha256 }) => ({
    name, version, filename, sha256,
  }));
  const manifest = {
    gitSha: resolveGitSha(),
    builtAt: new Date().toISOString(),
    pyodideVersion: PYODIDE_VERSION,
    files: entryNames,
    zipBytes: zipBytes.length,
    wheels,
  };

  mkdirSync(outDir, { recursive: true });
  writeFileSync(path.join(outDir, 'backend.zip'), zipBytes);
  writeFileSync(path.join(outDir, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
  console.error(`[bundle-backend] ${entryNames.length} 个文件 → ${path.join(outDir, 'backend.zip')} (${zipBytes.length} bytes)`);
}

main().catch((err) => {
  console.error(`[bundle-backend] 失败: ${err.stack || err}`);
  process.exit(1);
});
