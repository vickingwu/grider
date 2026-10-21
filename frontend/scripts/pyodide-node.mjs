/**
 * Node 侧 Pyodide 引擎装载工具（探针与分发一致性测试共用）。
 *
 * 职责：loadPyodide（npm 包）→ loadPackage → 把 wheel 写入 /wheels 并以 emfs: 路径 micropip 安装（deps=False）
 * → unpackArchive(backend.zip → /grider) → registerJsModule('grider_bridge') → bootstrap.init。
 * 取数桥 createFixtureBridge 从 backend/tests/fixtures/browser 读取正文；loadScriptVars 用 vm.runInNewContext
 * 执行脚本并读取 sandbox[name]，与浏览器 worker 的 importScripts 行为一致。
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import vm from 'node:vm';
import { loadPyodide } from 'pyodide';

const PYODIDE_CACHE_DIR = '.pyodide-cache';
const WHEELS_MOUNT = '/wheels';
const BACKEND_MOUNT = '/grider';
const EASTMONEY_FUND_HOST = 'fund.eastmoney.com';

/**
 * 与 Python 侧 browser_market_data.script_encoding_for 一致：天天基金脚本 UTF-8（带 BOM），其余 GBK。
 * @param {string} url
 * @returns {string}
 */
export function scriptEncodingFor(url) {
  return url.includes(EASTMONEY_FUND_HOST) ? 'utf-8' : 'gbk';
}

/**
 * 读取 fixture index.json。
 * @param {string} fixturesDir
 * @returns {{captured_at: string, entries: Record<string, string>}}
 */
export function readFixtureIndex(fixturesDir) {
  return JSON.parse(readFileSync(path.join(fixturesDir, 'index.json'), 'utf-8'));
}

/**
 * 构造 bootstrap.init 需要的 JS 桥（fixture 驱动，无网络）。
 * @param {string} fixturesDir
 * @returns {{httpGetBytes: Function, loadScriptVars: Function}}
 */
export function createFixtureBridge(fixturesDir) {
  const { entries } = readFixtureIndex(fixturesDir);

  const readBytes = (url) => {
    const filename = entries[url];
    if (filename === undefined) throw new Error(`fixture missing: ${url}`);
    return new Uint8Array(readFileSync(path.join(fixturesDir, filename)));
  };

  return {
    httpGetBytes(url, _timeoutMs, _ttl) {
      return readBytes(url);
    },
    loadScriptVars(url, namesJson, _ttl) {
      const names = JSON.parse(namesJson);
      const text = new TextDecoder(scriptEncodingFor(url)).decode(readBytes(url));
      const sandbox = {};
      vm.runInNewContext(text, sandbox, { filename: url, timeout: 10_000 });
      const out = {};
      for (const name of names) {
        if (sandbox[name] !== undefined) out[name] = sandbox[name];
      }
      return JSON.stringify(out);
    },
  };
}

/**
 * 装载 Pyodide 运行时并预装官方包。
 * @param {{packages: string[], log?: Function, cacheDir?: string}} options
 * @returns {Promise<{pyodide: any, timings: Record<string, number>}>}
 */
export async function loadRuntime({ packages, log = () => {}, cacheDir = PYODIDE_CACHE_DIR }) {
  const timings = {};
  let t = performance.now();
  const pyodide = await loadPyodide({
    packageCacheDir: cacheDir,
    stdout: (line) => log(`[py:stdout] ${line}`),
    stderr: (line) => log(`[py:stderr] ${line}`),
  });
  timings.runtimeMs = Math.round(performance.now() - t);
  log(`loadPyodide ok: ${pyodide.version} (${timings.runtimeMs} ms)`);

  t = performance.now();
  await pyodide.loadPackage(packages, { messageCallback: (m) => log(`[loadPackage] ${m}`) });
  timings.packagesMs = Math.round(performance.now() - t);
  log(`loadPackage(${JSON.stringify(packages)}) ok (${timings.packagesMs} ms)`);
  return { pyodide, timings };
}

/**
 * 把 dist/engine/wheels/*.whl 写入 /wheels 并通过 micropip（deps=False）安装。
 * @param {any} pyodide
 * @param {string} wheelsDir
 * @param {Function} log
 * @returns {Promise<string[]>} 安装的 wheel 文件名
 */
export async function installWheels(pyodide, wheelsDir, log = () => {}) {
  const filenames = readdirSync(wheelsDir).filter((f) => f.endsWith('.whl')).sort();
  if (!filenames.length) throw new Error(`没有 wheel: ${wheelsDir}（先运行 npm run build:engine）`);
  pyodide.FS.mkdirTree(WHEELS_MOUNT);
  const emfsPaths = [];
  for (const filename of filenames) {
    pyodide.FS.writeFile(`${WHEELS_MOUNT}/${filename}`, new Uint8Array(readFileSync(path.join(wheelsDir, filename))));
    emfsPaths.push(`emfs:${WHEELS_MOUNT}/${filename}`);
  }
  await pyodide.runPythonAsync(
    `import micropip\nawait micropip.install(${JSON.stringify(emfsPaths)}, deps=False)\n`,
  );
  log(`micropip.install(deps=False) ok: ${filenames.join(', ')}`);
  return filenames;
}

/**
 * 解压 backend.zip 到 /grider 并加入 sys.path。
 * @param {any} pyodide
 * @param {string} zipPath
 * @param {Function} log
 */
export function mountBackend(pyodide, zipPath, log = () => {}) {
  if (!existsSync(zipPath)) throw new Error(`没有 backend.zip: ${zipPath}（先运行 npm run build:engine）`);
  // Pyodide 不接受 Node Buffer 子类，需转为裸 Uint8Array
  const raw = readFileSync(zipPath);
  const zipBytes = new Uint8Array(raw.buffer, raw.byteOffset, raw.byteLength);
  pyodide.unpackArchive(zipBytes, 'zip', { extractDir: BACKEND_MOUNT });
  pyodide.runPython(`import sys\nif ${JSON.stringify(BACKEND_MOUNT)} not in sys.path:\n    sys.path.insert(0, ${JSON.stringify(BACKEND_MOUNT)})\n`);
  log(`unpackArchive ok: ${zipBytes.length} bytes → ${BACKEND_MOUNT}`);
}

/**
 * 完整装载在线引擎并返回 dispatch_json 包装。
 * @param {{engineDir: string, fixturesDir: string, packages: string[], log?: Function}} options
 * @returns {Promise<{pyodide: any, info: object, timings: Record<string, number>, dispatchJson: Function}>}
 */
export async function bootEngine({ engineDir, fixturesDir, packages, log = () => {} }) {
  const { pyodide, timings } = await loadRuntime({ packages, log });

  let t = performance.now();
  await installWheels(pyodide, path.join(engineDir, 'wheels'), log);
  timings.wheelsMs = Math.round(performance.now() - t);

  t = performance.now();
  mountBackend(pyodide, path.join(engineDir, 'backend.zip'), log);
  timings.backendMs = Math.round(performance.now() - t);

  t = performance.now();
  pyodide.registerJsModule('grider_bridge', createFixtureBridge(fixturesDir));
  const infoJson = pyodide.runPython(
    'import grider_bridge\nfrom app.browser import bootstrap\nbootstrap.init(grider_bridge)\n',
  );
  const info = JSON.parse(infoJson);
  timings.bootstrapMs = Math.round(performance.now() - t);
  timings.totalMs = Object.values(timings).reduce((a, b) => a + b, 0);
  log(`bootstrap.init ok: ${infoJson} (${timings.bootstrapMs} ms)`);

  const dispatchProxy = pyodide.runPython('from app.browser.dispatch import dispatch_json\ndispatch_json');
  // Pyodide 0.29 把 JS null 转成 JsNull（非 None），无请求体必须传 undefined 才会落到 Python 的 None
  const dispatchJson = (method, apiPath, query = '', body = null) =>
    JSON.parse(dispatchProxy(method, apiPath, query, body === null ? undefined : body));

  return { pyodide, info, timings, dispatchJson };
}
