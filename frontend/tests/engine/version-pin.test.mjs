/**
 * 单一 Pyodide 版本常量（design.md §10 表、NIT 26）：
 * Pyodide 版本号字面量在 src/、engine/、scripts/、tests/、.github/ 之下只允许出现在
 * src/shared/services/engine/engineConfig.js；package.json 的 devDependencies.pyodide 必须等于 PYODIDE_VERSION。
 * （package.json / package-lock.json 中的 pyodide@<版本> 固定不在扫描范围内。）
 */
import assert from 'node:assert/strict';
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';

const TEST_DIR = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(TEST_DIR, '..', '..');
const REPO_DIR = path.resolve(FRONTEND_DIR, '..');
const ENGINE_CONFIG_REL = path.join('src', 'shared', 'services', 'engine', 'engineConfig.js');
const SCAN_ROOTS = [
  path.join(FRONTEND_DIR, 'src'),
  path.join(FRONTEND_DIR, 'engine'),
  path.join(FRONTEND_DIR, 'scripts'),
  path.join(FRONTEND_DIR, 'tests'),
  path.join(REPO_DIR, '.github'),
];
const TEXT_EXTENSIONS = new Set(['.js', '.jsx', '.mjs', '.cjs', '.ts', '.json', '.yml', '.yaml', '.html', '.css', '.md', '.txt', '.env', '.py']);
const SKIP_DIRS = new Set(['node_modules', 'dist', '.pyodide-cache']);

/**
 * 递归列出可扫描的文本文件。
 * @param {string} dir
 * @param {string[]} acc
 * @returns {string[]}
 */
function listTextFiles(dir, acc) {
  if (!existsSync(dir)) return acc;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (!SKIP_DIRS.has(entry.name)) listTextFiles(full, acc);
      continue;
    }
    if (entry.isFile() && TEXT_EXTENSIONS.has(path.extname(entry.name))) acc.push(full);
  }
  return acc;
}

test('PYODIDE_VERSION literal appears only in engineConfig.js', async () => {
  const { PYODIDE_VERSION } = await import(pathToFileURL(path.join(FRONTEND_DIR, ENGINE_CONFIG_REL)).href);
  assert.match(PYODIDE_VERSION, /^\d+\.\d+\.\d+$/);

  const files = SCAN_ROOTS.flatMap((root) => listTextFiles(root, []));
  const offenders = files
    .filter((file) => readFileSync(file, 'utf-8').includes(PYODIDE_VERSION))
    .map((file) => path.relative(REPO_DIR, file).split(path.sep).join('/'))
    .filter((rel) => rel !== `frontend/${ENGINE_CONFIG_REL.split(path.sep).join('/')}`);
  assert.deepEqual(offenders, [], `Pyodide 版本号字面量只能出现在 engineConfig.js，违规文件: ${offenders.join(', ')}`);
  assert.ok(files.some((f) => f.endsWith(path.join('engine', 'engineConfig.js'))), 'engineConfig.js 未被扫描到');
});

test('package.json devDependencies.pyodide === PYODIDE_VERSION', async () => {
  const { PYODIDE_VERSION } = await import(pathToFileURL(path.join(FRONTEND_DIR, ENGINE_CONFIG_REL)).href);
  const pkg = JSON.parse(readFileSync(path.join(FRONTEND_DIR, 'package.json'), 'utf-8'));
  assert.equal(pkg.devDependencies.pyodide, PYODIDE_VERSION);
});
