/**
 * 验证项 3：Pyodide 分发器 == 原生 Flask（注入同一组 fixture 数据）。design.md §11.3，NIT 18，MEDIUM 2。
 *
 * 原生侧：node scripts/run-python.mjs -m app.browser.dispatch --fixtures <dir> --request <file>（每个请求一个新进程）。
 * Pyodide 侧：pyodide npm + dist/engine/{wheels,backend.zip} + Node fixture 桥（scripts/pyodide-node.mjs）。
 * 前置：npm run build:engine（test:engine 脚本已包含）。
 *
 * 比较规则：
 * - 先断言两侧 status 200 且 success === true
 * - 递归删除易变键 VOLATILE_KEYS
 * - 数值：若最近的外层键在 ROUNDED_KEYS 中（后端用 round(x, n) 输出），absTol = 0.5 × 10^-n；
 *   其余数值 relTol 1e-6 / absTol 1e-9（wasm 与 x86 的浮点求和顺序差异可能翻转 round 的进位）
 * - fish-basin：Pyodide 载荷必须有 unsupported == []（932000 中证2000 改走东财 push2his fixture），原生载荷无
 *   unsupported 键；932000 行两侧一致：status ∈ {YES,NO}，或 fixture 抓取失败时 error === '数据源繁忙，请稍后刷新'
 */
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { after, before, describe, test } from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { bootEngine, readFixtureIndex } from '../../scripts/pyodide-node.mjs';

const TEST_DIR = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(TEST_DIR, '..', '..');
const ENGINE_DIR = path.join(FRONTEND_DIR, 'dist', 'engine');
const FIXTURES_DIR = path.resolve(FRONTEND_DIR, '..', 'backend', 'tests', 'fixtures', 'browser');
const RUN_PYTHON = path.join(FRONTEND_DIR, 'scripts', 'run-python.mjs');
const ENGINE_CONFIG_URL = pathToFileURL(path.join(FRONTEND_DIR, 'src', 'shared', 'services', 'engine', 'engineConfig.js')).href;

const FIXTURE_MAX_AGE_DAYS = 10;
const MS_PER_DAY = 86_400_000;
const REL_TOL = 1e-6;
const ABS_TOL = 1e-9;
const EM_FALLBACK_CODE = '932000';
const BUSY_MESSAGE = '数据源繁忙，请稍后刷新';

/**
 * 两侧都会变化、与算法无关的键（递归删除）。design §11.3 列出前五个；
 * analysis_timestamp 是 etf_analysis_service 写入的 datetime.now().isoformat()（墙钟），首次运行时发现并补入。
 */
const VOLATILE_KEYS = new Set(['timestamp', 'generated_at', 'elapsed_seconds', 'data_time', 'from_cache', 'analysis_timestamp']);

/**
 * 后端 round(x, n) 输出的键 → 单位 10^-n（grep `round(` of backend/app/services/*.py；
 * backend/app/algorithms/backtest/metrics.py 没有 round( 调用）。同名键取各处最粗的单位。
 *
 * backtest_service.py：ma 3 | total_return 4 | annualized_return 4 | absolute_profit 2 | max_drawdown 4 |
 *   sharpe_ratio 2 | volatility 4 | win_rate 4 | profit_loss_ratio 2 | capital_utilization_rate 4 |
 *   grid_trigger_rate 4 | hold_return 4 | excess_return 4 | excess_return_rate 4 | lower 3 | upper 3 |
 *   step_size 4 | current_price 3 | ratio 4 | total_capital 2 | total_asset 2 | price 3 |
 *   profit_contribution 2 | commission 2 | profit 2 | cash 2
 * etf_analysis_service.py：lower 3 | upper 3 | ratio 4 | step_size 3 | step_ratio 4 | price_levels 3（列表元素）
 * data_service.py：change_pct 3
 * screener_service.py：atr_pct 2 | volatility_pct 1 | adx_value 1 | avg_amount 0
 * ma_screener_service.py：仅 elapsed（易变键）
 * fish_basin_service.py：见 FISH_BASIN_ROUNDED_KEYS（price/threshold/ma20 为整数，与回测的 price 冲突，单独一表）
 */
const ROUNDED_KEYS = {
  ma: 1e-3,
  total_return: 1e-4,
  annualized_return: 1e-4,
  absolute_profit: 1e-2,
  max_drawdown: 1e-4,
  sharpe_ratio: 1e-2,
  volatility: 1e-4,
  win_rate: 1e-4,
  profit_loss_ratio: 1e-2,
  capital_utilization_rate: 1e-4,
  grid_trigger_rate: 1e-4,
  hold_return: 1e-4,
  excess_return: 1e-4,
  excess_return_rate: 1e-4,
  lower: 1e-3,
  upper: 1e-3,
  step_size: 1e-3,
  step_ratio: 1e-4,
  current_price: 1e-3,
  ratio: 1e-4,
  total_capital: 1e-2,
  total_asset: 1e-2,
  price: 1e-3,
  price_levels: 1e-3,
  profit_contribution: 1e-2,
  commission: 1e-2,
  profit: 1e-2,
  cash: 1e-2,
  change_pct: 1e-3,
  atr_pct: 1e-2,
  volatility_pct: 1e-1,
  adx_value: 1e-1,
  avg_amount: 1,
};

/** fish_basin_service.py：change_pct 4 | price 0 | threshold 0 | ma20 0 | deviation 4 | vol_ratio 2 | range_pct 4 */
const FISH_BASIN_ROUNDED_KEYS = {
  change_pct: 1e-4,
  price: 1,
  threshold: 1,
  ma20: 1,
  deviation: 1e-4,
  vol_ratio: 1e-2,
  range_pct: 1e-4,
};

const ANALYZE_BODY = {
  etfCode: '510300', totalCapital: 100000, gridType: '等比', riskPreference: '均衡', adjustmentCoefficient: 1.0,
};
const FUND_MA_BODY = { etfCode: 'F110011', totalCapital: 100000, maParams: { period: 20, maType: 'SMA' } };

/**
 * 递归删除易变键（返回新对象）。
 * @param {any} value
 * @returns {any}
 */
function stripVolatile(value) {
  if (Array.isArray(value)) return value.map(stripVolatile);
  if (value && typeof value === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      if (VOLATILE_KEYS.has(k)) continue;
      out[k] = stripVolatile(v);
    }
    return out;
  }
  return value;
}

/**
 * 数值比较：rounded 键 absTol 0.5×unit，其余 relTol/absTol。
 * @param {number} a
 * @param {number} b
 * @param {number|undefined} unit
 * @returns {boolean}
 */
function numbersClose(a, b, unit) {
  if (Number.isNaN(a) && Number.isNaN(b)) return true;
  const diff = Math.abs(a - b);
  if (unit !== undefined) return diff <= 0.5 * unit + ABS_TOL;
  return diff <= Math.max(ABS_TOL, REL_TOL * Math.max(Math.abs(a), Math.abs(b)));
}

/**
 * 带容差的深比较；不一致时抛出带路径的 AssertionError。
 * @param {any} actual   Pyodide 侧
 * @param {any} expected 原生侧
 * @param {Record<string, number>} roundedKeys
 * @param {string} pathLabel
 * @param {number|undefined} unit 最近外层 rounded 键的单位（沿数组向下传递）
 */
function assertDeepClose(actual, expected, roundedKeys, pathLabel = '$', unit = undefined) {
  if (typeof actual === 'number' && typeof expected === 'number') {
    assert.ok(numbersClose(actual, expected, unit),
      `${pathLabel}: pyodide=${actual} native=${expected} (unit=${unit ?? 'rel ' + REL_TOL})`);
    return;
  }
  if (Array.isArray(expected)) {
    assert.ok(Array.isArray(actual), `${pathLabel}: 期望数组`);
    assert.equal(actual.length, expected.length, `${pathLabel}: 数组长度 pyodide=${actual.length} native=${expected.length}`);
    expected.forEach((item, i) => assertDeepClose(actual[i], item, roundedKeys, `${pathLabel}[${i}]`, unit));
    return;
  }
  if (expected && typeof expected === 'object') {
    assert.ok(actual && typeof actual === 'object' && !Array.isArray(actual), `${pathLabel}: 期望对象`);
    assert.deepEqual(Object.keys(actual).sort(), Object.keys(expected).sort(), `${pathLabel}: 键集合不同`);
    for (const [k, v] of Object.entries(expected)) {
      assertDeepClose(actual[k], v, roundedKeys, `${pathLabel}.${k}`, roundedKeys[k]);
    }
    return;
  }
  assert.deepEqual(actual, expected, `${pathLabel}: 值不同`);
}

/**
 * 原生分发：写请求文件并 spawn CLI；返回 {status, body(对象)}。
 * @param {string} tmpDir
 * @param {string} name
 * @param {{method: string, path: string, query?: string, body?: object|null}} request
 */
function dispatchNative(tmpDir, name, request) {
  const file = path.join(tmpDir, name);
  writeFileSync(file, JSON.stringify({ query: '', body: null, ...request }), 'utf-8');
  const result = spawnSync(process.execPath, [RUN_PYTHON, '-m', 'app.browser.dispatch', '--fixtures', FIXTURES_DIR, '--request', file], {
    encoding: 'utf-8', maxBuffer: 64 * 1024 * 1024, windowsHide: true,
  });
  assert.equal(result.status, 0, `原生 dispatch ${name} 退出码 ${result.status}\n${result.stderr.slice(-4000)}`);
  const jsonStart = result.stdout.indexOf('{');
  assert.ok(jsonStart >= 0, `原生 dispatch ${name} 无 JSON 输出:\n${result.stdout.slice(0, 2000)}`);
  const envelope = JSON.parse(result.stdout.slice(jsonStart));
  return { status: envelope.status, body: JSON.parse(envelope.body) };
}

/**
 * 断言 200 + success:true。
 * @param {{status: number, body: any}} resp
 * @param {string} label
 */
function assertOk(resp, label) {
  assert.equal(resp.status, 200, `${label}: status ${resp.status} ${JSON.stringify(resp.body).slice(0, 500)}`);
  assert.equal(resp.body.success, true, `${label}: success !== true ${JSON.stringify(resp.body).slice(0, 500)}`);
}

describe('dispatch parity: Pyodide vs native Flask', () => {
  let engine;
  let tmpDir;
  let nativeGridStrategy;

  const dispatchPyodide = (request) => {
    const body = request.body === undefined || request.body === null ? null : JSON.stringify(request.body);
    const envelope = engine.dispatchJson(request.method, request.path, request.query || '', body);
    return { status: envelope.status, body: JSON.parse(envelope.body) };
  };

  const runBoth = (name, request) => {
    const native = dispatchNative(tmpDir, name, request);
    const pyodide = dispatchPyodide(request);
    assertOk(native, `${name} native`);
    assertOk(pyodide, `${name} pyodide`);
    return { native, pyodide };
  };

  before(async () => {
    const index = readFixtureIndex(FIXTURES_DIR);
    const ageDays = (Date.now() - Date.parse(`${index.captured_at}T00:00:00Z`)) / MS_PER_DAY;
    assert.ok(ageDays <= FIXTURE_MAX_AGE_DAYS,
      `fixtures stale — run capture (captured_at=${index.captured_at}, ${ageDays.toFixed(1)} d > ${FIXTURE_MAX_AGE_DAYS} d)`);
    assert.ok(existsSync(path.join(ENGINE_DIR, 'backend.zip')), 'dist/engine/backend.zip 缺失 — 先运行 npm run build:engine');

    tmpDir = mkdtempSync(path.join(os.tmpdir(), 'grider-parity-'));
    const { PYODIDE_PACKAGES } = await import(ENGINE_CONFIG_URL);
    const started = performance.now();
    engine = await bootEngine({ engineDir: ENGINE_DIR, fixturesDir: FIXTURES_DIR, packages: PYODIDE_PACKAGES });
    console.error(`[parity] Pyodide engine ready in ${Math.round(performance.now() - started)} ms ${JSON.stringify(engine.timings)}`);
  });

  after(() => {
    if (tmpDir) rmSync(tmpDir, { recursive: true, force: true });
  });

  test('GET /api/version bodies equal minus timestamp', () => {
    const request = { method: 'GET', path: '/api/version' };
    const { native, pyodide } = runBoth('version.json', request);
    assert.deepEqual(stripVolatile(pyodide.body), stripVolatile(native.body));
  });

  test('POST /api/grid/analyze 510300 等比/均衡/1.0', () => {
    const request = { method: 'POST', path: '/api/grid/analyze', body: ANALYZE_BODY };
    const { native, pyodide } = runBoth('analyze.json', request);
    nativeGridStrategy = native.body.data.grid_strategy;
    assert.ok(nativeGridStrategy, 'native analyze 缺少 data.grid_strategy');
    assertDeepClose(stripVolatile(pyodide.body), stripVolatile(native.body), ROUNDED_KEYS);
  });

  test('POST /api/grid/backtest with the native gridStrategy', () => {
    assert.ok(nativeGridStrategy, '依赖 analyze 测试先通过');
    const request = {
      method: 'POST',
      path: '/api/grid/backtest',
      body: {
        etfCode: '510300', exchangeCode: 'XSHG', gridStrategy: nativeGridStrategy,
        type: 'ETF', backtestConfig: null, customGridParams: null,
      },
    };
    const { native, pyodide } = runBoth('backtest.json', request);
    assertDeepClose(stripVolatile(pyodide.body), stripVolatile(native.body), ROUNDED_KEYS);
  });

  test('GET /api/info/F110011', () => {
    const request = { method: 'GET', path: '/api/info/F110011' };
    const { native, pyodide } = runBoth('fund_info.json', request);
    assertDeepClose(stripVolatile(pyodide.body), stripVolatile(native.body), ROUNDED_KEYS);
  });

  test('POST /api/grid/ma-backtest F110011 period 20 SMA', () => {
    const request = { method: 'POST', path: '/api/grid/ma-backtest', body: FUND_MA_BODY };
    const { native, pyodide } = runBoth('fund_ma.json', request);
    assertDeepClose(stripVolatile(pyodide.body), stripVolatile(native.body), ROUNDED_KEYS);
  });

  test('GET /api/screener/fish-basin?board=index (MEDIUM 2)', () => {
    const request = { method: 'GET', path: '/api/screener/fish-basin', query: 'board=index' };
    const { native, pyodide } = runBoth('fishbasin_index.json', request);

    const pyData = stripVolatile(pyodide.body.data);
    const nativeData = stripVolatile(native.body.data);
    assert.ok(Array.isArray(pyData.unsupported), 'Pyodide 载荷缺少 unsupported[]');
    assert.deepEqual(pyData.unsupported, [], `Pyodide 载荷 unsupported 应为空: ${JSON.stringify(pyData.unsupported)}`);
    assert.equal('unsupported' in nativeData, false, '原生载荷不应有 unsupported 键');
    delete pyData.unsupported;

    const pyRow = pyData.results.find((r) => r.code === EM_FALLBACK_CODE);
    const nativeRow = nativeData.results.find((r) => r.code === EM_FALLBACK_CODE);
    assert.ok(pyRow && nativeRow, `缺少 ${EM_FALLBACK_CODE} 行`);
    const emOk = (row) => row.status === 'YES' || row.status === 'NO';
    const emBusy = (row) => row.status === null && row.error === BUSY_MESSAGE;
    assert.ok(emOk(pyRow) || emBusy(pyRow), `${EM_FALLBACK_CODE} 行应为 YES/NO 或繁忙: ${JSON.stringify(pyRow)}`);
    assert.equal(emOk(pyRow), emOk(nativeRow), `${EM_FALLBACK_CODE} 行两侧状态类别不一致`);
    if (emBusy(pyRow)) console.error(`[parity] ${EM_FALLBACK_CODE} 东财 fixture 缺失，两侧均为「${BUSY_MESSAGE}」`);

    assert.ok(nativeData.results.length >= 12, `fish-basin 结果过少: ${nativeData.results.length}`);
    assertDeepClose(pyData, nativeData, FISH_BASIN_ROUNDED_KEYS);
  });
});
