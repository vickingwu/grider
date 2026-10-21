#!/usr/bin/env node
/**
 * 在 backend/ 目录下调用后端 venv 的 Python，参数原样透传，退出码跟随子进程。
 *
 * 解释器解析顺序：环境变量 GRIDER_PY → ../backend/.venv/Scripts/python.exe（Windows）或
 * ../backend/.venv/bin/python（其他平台）。禁止使用裸 `python`（Windows 上是商店占位程序）。
 *
 * 用法：node scripts/run-python.mjs -m tests.browser.capture_fixtures
 */
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
export const BACKEND_DIR = path.resolve(SCRIPT_DIR, '..', '..', 'backend');

/**
 * 解析后端 Python 解释器路径。
 * @returns {string}
 */
export function resolvePython() {
  if (process.env.GRIDER_PY) return process.env.GRIDER_PY;
  const venvPython = process.platform === 'win32'
    ? path.join(BACKEND_DIR, '.venv', 'Scripts', 'python.exe')
    : path.join(BACKEND_DIR, '.venv', 'bin', 'python');
  return venvPython;
}

/**
 * 子进程环境：关闭控制台 JSON 日志（setup_logger 否则会写 stdout），强制 UTF-8。
 * @returns {NodeJS.ProcessEnv}
 */
export function pythonEnv() {
  return { ...process.env, LOG_TO_CONSOLE: 'false', PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' };
}

/**
 * 同步运行后端 Python 并捕获 stdout/stderr（供测试使用）。
 * @param {string[]} args
 * @returns {{status: number|null, stdout: string, stderr: string}}
 */
export function runPythonCapture(args) {
  const result = spawnSync(resolvePython(), args, {
    cwd: BACKEND_DIR,
    env: pythonEnv(),
    encoding: 'utf-8',
    maxBuffer: 64 * 1024 * 1024,
    windowsHide: true,
  });
  if (result.error) throw result.error;
  return { status: result.status, stdout: result.stdout, stderr: result.stderr };
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const python = resolvePython();
  if (!existsSync(python)) {
    console.error(`[run-python] 未找到 Python 解释器: ${python}（可用 GRIDER_PY 环境变量指定）`);
    process.exit(2);
  }
  const result = spawnSync(python, process.argv.slice(2), {
    cwd: BACKEND_DIR,
    env: pythonEnv(),
    stdio: 'inherit',
    windowsHide: true,
  });
  if (result.error) {
    console.error(`[run-python] 启动失败: ${result.error.message}`);
    process.exit(2);
  }
  process.exit(result.status ?? 1);
}
