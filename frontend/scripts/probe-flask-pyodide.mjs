#!/usr/bin/env node
/**
 * Flask-in-Pyodide 可行性探针（design.md §5.2.4，限时 2 小时；作为决策记录保留）。
 *
 * P1：loadPyodide（npm 包，packageCacheDir .pyodide-cache）→ loadPackage(PYODIDE_FLASK_PACKAGES)
 *     → emfs: 安装 4 个 wheel（micropip, deps=False）→ import flask, werkzeug → Flask('p') 的
 *     test_client().get('/api/version') → 期望 200。
 * P2：再装 numpy/pandas → unpackArchive(backend.zip) → bootstrap.init(fixture 桥)
 *     → dispatch_json GET /api/info/capital、POST /api/grid/analyze 510300 → 期望 200 且 success:true。
 *
 * 任一阶段遇到标准库 ModuleNotFoundError：按 NIT 25 把当前 Pyodide lock 中独立发布的包
 * （ssl/hashlib/lzma/sqlite3/pydecimal）加入 engineConfig.PYODIDE_PACKAGES 后重跑；禁止 sys.modules 桩
 * （除非没有任何 Pyodide 包提供该模块，并在注释中写明模块名）。
 *
 * 前置：npm run build:engine（dist/engine/backend.zip + wheels/）。输出以 "P1 PASS"/"P2 PASS" 结尾，
 * 并打印最终 PACKAGES 列表与各阶段耗时。失败 → exit 1 并打印 traceback。
 */
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { bootEngine, installWheels, loadRuntime } from './pyodide-node.mjs';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(SCRIPT_DIR, '..');
const ENGINE_DIR = path.join(FRONTEND_DIR, 'dist', 'engine');
const FIXTURES_DIR = path.resolve(FRONTEND_DIR, '..', 'backend', 'tests', 'fixtures', 'browser');
const ENGINE_CONFIG_URL = pathToFileURL(path.join(FRONTEND_DIR, 'src', 'shared', 'services', 'engine', 'engineConfig.js')).href;

const ANALYZE_BODY = JSON.stringify({
  etfCode: '510300', totalCapital: 100000, gridType: '等比', riskPreference: '均衡', adjustmentCoefficient: 1.0,
});

const log = (line) => process.stdout.write(`${line}\n`);

/**
 * P1：纯 Flask 栈在 Pyodide 中通过 test_client 响应。
 * @param {string[]} packages
 */
async function probeP1(packages) {
  log(`== P1: Flask test_client in Pyodide (packages=${JSON.stringify(packages)})`);
  const started = performance.now();
  const { pyodide, timings } = await loadRuntime({ packages, log });
  await installWheels(pyodide, path.join(ENGINE_DIR, 'wheels'), log);
  const result = pyodide.runPython(`
import json
from importlib.metadata import version as _pkg_version
import flask, werkzeug  # noqa: F401  确认两包均可导入
from flask import Flask, jsonify
_app = Flask('p')

@_app.get('/api/version')
def _version():
    return jsonify(version='probe', flask=_pkg_version('flask'), werkzeug=_pkg_version('werkzeug'))

_resp = _app.test_client().get('/api/version')
json.dumps({'status': _resp.status_code, 'body': _resp.get_data(as_text=True)})
`);
  const parsed = JSON.parse(result);
  log(`P1 response: ${JSON.stringify(parsed)}`);
  log(`P1 timings: ${JSON.stringify(timings)} total=${Math.round(performance.now() - started)} ms`);
  if (parsed.status !== 200) throw new Error(`P1 expected 200, got ${parsed.status}`);
  log('P1 PASS');
}

/**
 * P2：完整后端（numpy/pandas + backend.zip + bootstrap.init）通过 dispatch_json 响应。
 * @param {string[]} packages
 */
async function probeP2(packages) {
  log(`== P2: full backend via bootstrap.init + dispatch_json (packages=${JSON.stringify(packages)})`);
  const { info, timings, dispatchJson } = await bootEngine({
    engineDir: ENGINE_DIR, fixturesDir: FIXTURES_DIR, packages, log,
  });
  log(`P2 bootstrap info: ${JSON.stringify(info)}`);

  const t1 = performance.now();
  const capital = dispatchJson('GET', '/api/info/capital');
  log(`P2 GET /api/info/capital → ${capital.status} (${Math.round(performance.now() - t1)} ms) ${capital.body.slice(0, 200)}`);
  const capitalBody = JSON.parse(capital.body);
  if (capital.status !== 200 || capitalBody.success !== true) {
    throw new Error(`P2 /api/info/capital failed: ${capital.status} ${capital.body}`);
  }

  const t2 = performance.now();
  const analyze = dispatchJson('POST', '/api/grid/analyze', '', ANALYZE_BODY);
  log(`P2 POST /api/grid/analyze → ${analyze.status} (${Math.round(performance.now() - t2)} ms) ${analyze.body.slice(0, 200)}`);
  const analyzeBody = JSON.parse(analyze.body);
  if (analyze.status !== 200 || analyzeBody.success !== true) {
    throw new Error(`P2 /api/grid/analyze failed: ${analyze.status} ${analyze.body}`);
  }
  log(`P2 timings: ${JSON.stringify(timings)}`);
  log('P2 PASS');
}

async function main() {
  const { PYODIDE_FLASK_PACKAGES, PYODIDE_PACKAGES, PYODIDE_VERSION } = await import(ENGINE_CONFIG_URL);
  log(`probe-flask-pyodide: pyodide npm ${PYODIDE_VERSION}, node ${process.version}, ${new Date().toISOString()}`);
  await probeP1(PYODIDE_FLASK_PACKAGES);
  await probeP2(PYODIDE_PACKAGES);
  log(`FINAL PACKAGES: ${JSON.stringify(PYODIDE_PACKAGES)}`);
  log('DECISION: test_client (no fallback dispatcher)');
}

main().then(
  () => process.exit(0),
  (err) => {
    log(`PROBE FAILED: ${err && err.stack ? err.stack : err}`);
    process.exit(1);
  },
);
