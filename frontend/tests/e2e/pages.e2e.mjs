#!/usr/bin/env node
/**
 * GitHub Pages 在线版端到端验证（design.md §11.4 + MEDIUM 2 步骤 4b；NIT 20/28）。
 *
 * 用 Playwright chromium（headless）驱动 Pages 构建产物，每一项检查都以
 * {step, name, pass, detail} 写入 JSON（不截图）；鱼盆两个看板的逐项结果另存一份。
 *
 * 用法：
 *   node tests/e2e/pages.e2e.mjs --base http://127.0.0.1:8010/grider/ [--out <dir>] [--cdp <url>] [--cdn-local|--cdn-remote]
 *   node tests/e2e/pages.e2e.mjs --base https://vickingwu.github.io/grider/ --live
 *
 * - --base      站点根地址（必填，以 / 结尾）
 * - --live      线上模式：只跑步骤 1、2、4、4b、6、7，输出 e2e-live.json / fish-basin-live-prod.json
 * - --cdp       连接已运行的 Chrome（chromium.connectOverCDP），默认 chromium.launch({headless: true})
 * - --out       输出目录（默认环境变量 GRIDER_E2E_OUT，再默认 frontend/dist/e2e）
 * - --cdn-local 强制用本地镜像回答 cdn.jsdelivr.net/pyodide/* （本机 headless Chromium 对 jsdelivr 会 ERR_CONNECTION_RESET，
 *               而 Node fetch 正常；镜像优先读 node_modules/pyodide 与 .pyodide-cache，缺失的文件由 Node 代为下载真实 CDN 文件，
 *               产品 worker 代码与请求的 URL 不变）；--cdn-remote 禁止镜像；默认先探测，探测失败才启用镜像并记录
 *
 * 退出码：任一 pass === false → 1。
 */
import { chromium } from 'playwright';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PYODIDE_INDEX_URL } from '../../src/shared/services/engine/engineConfig.js';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(SCRIPT_DIR, '..', '..');

const INIT_STALL_MS = 180_000;
const INIT_CAP_MS = 900_000;
const SCREENER_CAP_MS = 900_000;
const SCREENER_PREFETCH_NOTE_S = 120;
const API_WAIT_MS = 180_000;
const UI_WAIT_MS = 30_000;
const PROBE_WAIT_MS = 240_000;
const CDN_PROBE_MS = 15_000;
const POLL_MS = 1_000;
const MIN_INDEX_ROWS = 12;
const MIN_SECTOR_ROWS = 10;
const MIN_SCREENER_ROWS = 40;
const ETF_CODE = '510300';
const UNSUPPORTED_INDEX_CODE = '932000';
const UNSUPPORTED_INDEX_NAME = '中证2000';
const UNSUPPORTED_PREFIX = '在线版暂不支持';
const SECTOR_UNSUPPORTED_NAMES = ['半导体', '航空航天', '机器人', '创新药', '中证中药', '光伏产业', '房地产', '中证旅游'];
const FAILURE_TEXTS = ['分析失败', '数据不完整', '回测失败'];
const CONSOLE_ALLOWLIST = [/fonts\.googleapis/, /cloudflareinsights/, /favicon|vite\.svg/];
/**
 * 深链接（/grider/screener 等）由 404.html 回退承载：GitHub Pages 与 serve-pages 都以 404 状态返回 index.html 正文
 * （design §7.4），Chromium 会为这个「文档本身」打一条 "Failed to load resource: ... 404" console error。
 * 这是部署机制而非应用错误，仅当 (a) 文本是 404 资源错误、(b) URL 正是本次以 404 状态落地的 document 请求时才忽略；
 * 忽略的条目与命中的文档 URL 都写入 JSON（spaFallback404Docs），其余 404 一律计入。
 */
const SPA_FALLBACK_404_TEXT = /Failed to load resource: the server responded with a status of 404/;
const CDN_PATTERN = /^https:\/\/cdn\.jsdelivr\.net\/pyodide\/v\d+\.\d+\.\d+\/full\/(.+)$/;
const CDN_HOST = 'cdn.jsdelivr.net';
const CDN_MIME = {
  '.js': 'text/javascript',
  '.mjs': 'text/javascript',
  '.wasm': 'application/wasm',
  '.zip': 'application/zip',
  '.json': 'application/json',
  '.whl': 'application/octet-stream',
  '.map': 'application/json',
};
const LIVE_STEPS = new Set(['1', '2', '4', '4b', '6', '7']);

// ---------- 参数 ----------

/**
 * @param {string[]} argv
 * @returns {{base: string, live: boolean, cdp: string|null, out: string, cdnMode: 'auto'|'local'|'remote'}}
 */
function parseArgs(argv) {
  const opts = { base: '', live: false, cdp: null, out: '', cdnMode: 'auto' };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--base') opts.base = argv[++i] || '';
    else if (arg === '--live') opts.live = true;
    else if (arg === '--cdp') opts.cdp = argv[++i] || null;
    else if (arg === '--out') opts.out = argv[++i] || '';
    else if (arg === '--cdn-local') opts.cdnMode = 'local';
    else if (arg === '--cdn-remote') opts.cdnMode = 'remote';
  }
  if (!opts.base) {
    console.error('缺少 --base <url>');
    process.exit(2);
  }
  if (!opts.base.endsWith('/')) opts.base += '/';
  if (!opts.out) opts.out = process.env.GRIDER_E2E_OUT || path.join(FRONTEND_DIR, 'dist', 'e2e');
  return opts;
}

const ARGS = parseArgs(process.argv.slice(2));
const ORIGIN = new URL(ARGS.base).origin;
const RESULT_FILE = path.join(ARGS.out, ARGS.live ? 'e2e-live.json' : 'e2e-local.json');
const FISH_FILE = path.join(ARGS.out, ARGS.live ? 'fish-basin-live-prod.json' : 'fish-basin-live.json');

// ---------- 结果收集 ----------

/** @type {Array<{step: string, name: string, pass: boolean, detail: string}>} */
const results = [];
const report = {
  startedAt: new Date().toISOString(),
  base: ARGS.base,
  live: ARGS.live,
  cdnMode: null,
  cdnServed: [],
  consoleAllowlist: [...CONSOLE_ALLOWLIST.map(String), `SPA 深链接文档自身的 404（404.html 回退，design §7.4）：${SPA_FALLBACK_404_TEXT} 且 url ∈ spaFallback404Docs`],
  spaFallback404Docs: [],
  pageErrors: [],
  consoleErrors: [],
  consoleErrorsIgnored: [],
  dialogs: [],
  engine: null,
  engineRequests: [],
  results,
};

/**
 * 记录一条断言。
 * @param {string} step
 * @param {string} name
 * @param {boolean} pass
 * @param {string} detail
 */
function check(step, name, pass, detail) {
  results.push({ step, name, pass: Boolean(pass), detail: String(detail ?? '') });
  console.log(`[${pass ? 'PASS' : 'FAIL'}] step ${step} · ${name} — ${detail}`);
}

/** @param {string} step */
function shouldRun(step) {
  return !ARGS.live || LIVE_STEPS.has(step);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * 当前北京时间是否处于 A 股连续竞价时段（工作日 09:30–15:00）。
 * @returns {{inSession: boolean, label: string}}
 */
function beijingSession() {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai', hour12: false, weekday: 'short', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).formatToParts(new Date());
  const get = (type) => parts.find((p) => p.type === type)?.value || '';
  const hour = Number(get('hour')) % 24;
  const minute = Number(get('minute'));
  const minutes = hour * 60 + minute;
  const weekday = get('weekday');
  const isWeekday = !['Sat', 'Sun'].includes(weekday);
  const inSession = isWeekday && minutes >= 9 * 60 + 30 && minutes < 15 * 60;
  const label = `${get('year')}-${get('month')}-${get('day')} ${String(hour).padStart(2, '0')}:${get('minute')} ${weekday} 北京时间`;
  return { inSession, label };
}

// ---------- CDN 本地镜像（见文件头说明） ----------

/**
 * @param {string} name
 * @returns {string|null}
 */
function localCdnFile(name) {
  for (const dir of [path.join(FRONTEND_DIR, 'node_modules', 'pyodide'), path.join(FRONTEND_DIR, '.pyodide-cache')]) {
    const candidate = path.join(dir, name);
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

/**
 * 在浏览器里探测 jsdelivr 是否可达（Node 可达不代表 Chromium 可达）。
 * @param {import('playwright').BrowserContext} context
 * @returns {Promise<{ok: boolean, detail: string}>}
 */
async function probeCdnFromBrowser(context) {
  const page = await context.newPage();
  try {
    await page.goto(ARGS.base, { waitUntil: 'commit' });
    const outcome = await page.evaluate(async ({ url, timeoutMs }) => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const res = await fetch(url, { method: 'HEAD', cache: 'no-store', signal: controller.signal });
        return { ok: res.ok, detail: `HTTP ${res.status}` };
      } catch (e) {
        return { ok: false, detail: String(e && e.message ? e.message : e) };
      } finally {
        clearTimeout(timer);
      }
    }, { url: `${PYODIDE_INDEX_URL}pyodide.js`, timeoutMs: CDN_PROBE_MS });
    return outcome;
  } catch (e) {
    return { ok: false, detail: String(e && e.message ? e.message : e) };
  } finally {
    await page.close();
  }
}

/**
 * @param {import('playwright').BrowserContext} context
 */
async function installCdnMirror(context) {
  await context.route(CDN_PATTERN, async (route) => {
    const url = route.request().url();
    const name = url.match(CDN_PATTERN)[1].split('?')[0];
    const headers = { 'Content-Type': CDN_MIME[path.extname(name)] || 'application/octet-stream', 'Access-Control-Allow-Origin': '*' };
    const local = localCdnFile(name);
    if (local) {
      const body = readFileSync(local);
      report.cdnServed.push({ name, from: 'local', bytes: body.length });
      return route.fulfill({ status: 200, body, headers });
    }
    try {
      const res = await fetch(url);
      const body = Buffer.from(await res.arrayBuffer());
      report.cdnServed.push({ name, from: 'node-fetch', status: res.status, bytes: body.length });
      return route.fulfill({ status: res.status, body, headers });
    } catch (e) {
      report.cdnServed.push({ name, from: 'node-fetch', error: String(e && e.message ? e.message : e) });
      return route.fulfill({ status: 502, body: 'cdn mirror fetch failed' });
    }
  });
}

// ---------- 页面侧钩子：观察 worker 往返（不改产品代码） ----------

/**
 * 以 addInitScript 注入：包装全局 Worker，记录 request→response 并保留最近一次鱼盆 payload。
 * 挂到 window.__griderE2E = { requests: [], fishBasin: {index, sector, lastRefresh}, fishBasinCount }。
 */
const WORKER_HOOK = `(() => {
  const OriginalWorker = window.Worker;
  const hook = { requests: [], pending: new Map(), fishBasin: {}, fishBasinCount: 0, lastFishBasin: null };
  window.__griderE2E = hook;
  window.Worker = class extends OriginalWorker {
    constructor(...args) {
      super(...args);
      this.addEventListener('message', (event) => {
        const msg = event.data || {};
        if (msg.type !== 'response' && msg.type !== 'error') return;
        const req = hook.pending.get(msg.id);
        if (!req) return;
        hook.pending.delete(msg.id);
        const record = { ...req, status: msg.status ?? null, error: msg.type === 'error' ? msg.message : null, ms: Math.round(performance.now() - req.t0), bytes: msg.body ? msg.body.length : 0 };
        delete record.t0;
        hook.requests.push(record);
        if (msg.type === 'response' && req.path === '/api/screener/fish-basin') {
          try {
            const payload = JSON.parse(msg.body);
            const board = (new URLSearchParams(req.query).get('board')) || 'index';
            hook.fishBasin[board] = payload;
            hook.lastFishBasin = { board, payload };
            hook.fishBasinCount += 1;
          } catch (e) { /* 非 JSON 响应忽略 */ }
        }
      });
    }
    postMessage(message, ...rest) {
      if (message && message.type === 'request') {
        hook.pending.set(message.id, { id: message.id, method: message.method, path: message.path, query: message.query || '', t0: performance.now() });
      }
      return super.postMessage(message, ...rest);
    }
  };
})();`;

// ---------- 通用等待 ----------

/**
 * 进度型等待：fn 返回 {done, progressKey, detail}；progressKey 连续 stallMs 不变则失败，总时长不超过 capMs。
 * @param {() => Promise<{done: boolean, progressKey: string, detail?: string}>} fn
 * @param {{stallMs: number, capMs: number, what: string}} opts
 */
async function waitWithProgress(fn, { stallMs, capMs, what }) {
  const started = Date.now();
  let lastKey = null;
  let lastChange = started;
  for (;;) {
    const state = await fn();
    if (state.done) return state;
    if (state.progressKey !== lastKey) {
      lastKey = state.progressKey;
      lastChange = Date.now();
    }
    const now = Date.now();
    if (now - lastChange > stallMs) throw new Error(`${what}：${Math.round(stallMs / 1000)} s 无进度（${state.detail || lastKey}）`);
    if (now - started > capMs) throw new Error(`${what}：超过硬上限 ${Math.round(capMs / 1000)} s（${state.detail || lastKey}）`);
    await sleep(POLL_MS);
  }
}

/**
 * 等待引擎横幅进入 ready（进度型：stage/bytes 180 s 不变即失败，硬上限 900 s）。
 * @param {import('playwright').Page} page
 * @returns {Promise<object>} window.__griderEngine 快照
 */
async function waitForEngine(page) {
  await page.waitForSelector('[data-engine-status]', { timeout: UI_WAIT_MS });
  await waitWithProgress(async () => {
    const snap = await page.evaluate(() => {
      const el = document.querySelector('[data-engine-status]');
      const engine = window.__griderEngine;
      return {
        status: el ? el.getAttribute('data-engine-status') : null,
        stage: engine ? engine.stage : null,
        bytes: engine ? engine.transferBytes : 0,
        error: engine ? engine.error : null,
        banner: el ? el.textContent : '',
      };
    });
    if (snap.status === 'error') throw new Error(`引擎初始化失败：${snap.error || snap.banner}`);
    return { done: snap.status === 'ready', progressKey: `${snap.stage}:${snap.bytes}`, detail: snap.banner };
  }, { stallMs: INIT_STALL_MS, capMs: INIT_CAP_MS, what: '引擎加载' });
  return page.evaluate(() => {
    const e = window.__griderEngine;
    return { status: e.status, stage: e.stage, elapsedMs: e.elapsedMs, transferBytes: e.transferBytes, info: e.info };
  });
}

/**
 * 等待 worker 请求队列清空（钩子 pending 为空，并稳定 1 s）。
 * @param {import('playwright').Page} page
 */
async function waitForEngineIdle(page) {
  for (let i = 0; i < 2; i += 1) {
    await page.waitForFunction(() => window.__griderE2E && window.__griderE2E.pending.size === 0, null, { timeout: API_WAIT_MS });
    await sleep(1000);
  }
}

/**
 * 等待 body 文本满足正则。
 * @param {import('playwright').Page} page
 * @param {RegExp} re
 * @param {number} timeout
 */
async function waitForBodyText(page, re, timeout) {
  await page.waitForFunction((source) => new RegExp(source).test(document.body.innerText), re.source, { timeout });
}

/**
 * @param {import('playwright').Page} page
 * @returns {Promise<string>}
 */
const bodyText = (page) => page.evaluate(() => document.body.innerText);

/**
 * 读取鱼盆表格每行 {code, name, status, title}。
 * @param {import('playwright').Page} page
 */
function readFishRows(page) {
  return page.evaluate(() => Array.from(document.querySelectorAll('tbody tr')).map((tr) => {
    const cells = tr.querySelectorAll('td');
    const statusCell = cells[8];
    const span = statusCell ? statusCell.querySelector('span') : null;
    return {
      code: cells[1] ? cells[1].textContent.trim() : '',
      name: cells[2] ? cells[2].textContent.trim() : '',
      status: span ? span.textContent.trim() : '',
      title: span ? span.getAttribute('title') : null,
    };
  }));
}

/**
 * 等待鱼盆当前看板计算完成（刷新按钮恢复可用且表格有行）。
 * @param {import('playwright').Page} page
 */
async function waitFishLoaded(page) {
  await page.waitForFunction(() => {
    const btn = Array.from(document.querySelectorAll('button')).find((b) => /^(刷新|计算中)/.test(b.textContent.trim()));
    return btn && !btn.disabled && btn.textContent.trim() === '刷新' && document.querySelectorAll('tbody tr').length > 0;
  }, null, { timeout: API_WAIT_MS });
}

/**
 * 把一个鱼盆 payload 压成 {code, status, is_realtime, error} 列表。
 * @param {object|null} payload
 */
function summarizeBoard(payload) {
  const data = payload && payload.data ? payload.data : null;
  if (!data) return null;
  const rows = (data.results || []).map((r) => ({ code: r.code, name: r.name, status: r.status ?? null, is_realtime: Boolean(r.is_realtime), error: r.error ?? null }));
  return {
    generated_at: data.generated_at ?? null,
    from_cache: data.from_cache ?? null,
    realtime_count: data.realtime_count ?? null,
    elapsed_seconds: data.elapsed_seconds ?? null,
    market_state: data.market_state ?? null,
    unsupported: data.unsupported ?? null,
    supported: rows.filter((r) => r.status === 'YES' || r.status === 'NO').length,
    total: rows.length,
    results: rows,
  };
}

// ---------- 主流程 ----------

/**
 * @param {import('playwright').Page} page
 * @param {() => number} cdnBytes
 */
async function step1Engine(page, cdnBytes) {
  const t0 = Date.now();
  await page.goto(ARGS.base, { waitUntil: 'domcontentloaded' });
  const engine = await waitForEngine(page);
  report.engine = { ...engine, wallMs: Date.now() - t0, playwrightCdnBytes: cdnBytes(), cdnMode: report.cdnMode };
  check('1', '引擎就绪（data-engine-status=ready）', engine.status === 'ready',
    `worker elapsedMs=${engine.elapsedMs} transferBytes=${engine.transferBytes} · Playwright request.sizes(cdn.jsdelivr.net)=${cdnBytes()} B · wall=${report.engine.wallMs} ms · cdnMode=${report.cdnMode} · info=${JSON.stringify(engine.info)}`);
}

/**
 * @param {import('playwright').Page} page
 */
async function step2Analysis(page) {
  const input = page.getByPlaceholder(/请输入标的代码/);
  await input.waitFor({ timeout: UI_WAIT_MS });
  await input.fill('');
  await input.fill(ETF_CODE);
  await page.evaluate(() => { window.__griderE2EMarker = 'same-document'; });
  await page.getByRole('button', { name: '开始分析策略' }).click();
  await page.waitForURL((u) => u.pathname.endsWith(`/analysis/${ETF_CODE}`) && u.search.length > 0, { timeout: UI_WAIT_MS });
  const nav = await page.evaluate(() => ({ marker: window.__griderE2EMarker, navigations: performance.getEntriesByType('navigation').length, href: location.href }));
  const expectedPrefix = `${ARGS.base}analysis/${ETF_CODE}?`;
  check('2', '提交后客户端路由到 /analysis/510300?（无整页刷新）',
    nav.href.startsWith(expectedPrefix) && nav.marker === 'same-document' && nav.navigations === 1,
    `href=${nav.href} marker=${nav.marker} navigationEntries=${nav.navigations}`);

  await waitForBodyText(page, /\d+\/100分/, API_WAIT_MS);
  await waitForBodyText(page, /网格价格区间/, UI_WAIT_MS);
  const overview = await bodyText(page);
  const score = overview.match(/(\d+)\/100分/);
  check('2', '适宜度评分与网格参数卡片渲染', Boolean(score) && /网格价格区间/.test(overview) && /网格步长/.test(overview), `total_score=${score ? score[1] : 'n/a'}`);

  await page.getByRole('button', { name: '回测分析' }).click();
  await waitForBodyText(page, /核心指标/, API_WAIT_MS);
  await waitForBodyText(page, /区间收益率[\s\S]*最大回撤[\s\S]*夏普比率/, API_WAIT_MS);
  await page.waitForFunction(() => !/正在回测|回测中/.test(document.body.innerText), null, { timeout: API_WAIT_MS }).catch(() => {});
  const text = await bodyText(page);
  const found = FAILURE_TEXTS.filter((t) => text.includes(t));
  check('2', '回测分析标签渲染 BacktestMetrics，且无 分析失败/数据不完整/回测失败', found.length === 0 && /区间收益率/.test(text), found.length ? `出现：${found.join('、')}` : '核心指标/基准对比/交易统计已渲染');
}

/**
 * @param {import('playwright').Page} page
 */
async function step3CustomBacktestAndExport(page) {
  const today = new Date();
  const start = new Date(today.getTime() - 120 * 86400_000);
  const ymd = (d) => d.toISOString().slice(0, 10);

  await page.getByRole('button', { name: '编辑参数' }).click();
  const dateInputs = page.locator('input[type="date"]');
  await dateInputs.first().waitFor({ timeout: UI_WAIT_MS });
  await dateInputs.nth(0).fill(ymd(start));
  await dateInputs.nth(1).fill(ymd(today));
  const beforeMetrics = await page.evaluate(() => document.body.innerText.match(/区间收益率[\s\S]{0,80}/)?.[0] || '');
  const beforeCount = await page.evaluate(() => (window.__griderE2E ? window.__griderE2E.requests.filter((r) => r.path === '/api/grid/backtest').length : -1));
  await page.getByRole('button', { name: '保存并重新回测' }).click();
  await page.waitForFunction((n) => window.__griderE2E && window.__griderE2E.requests.filter((r) => r.path === '/api/grid/backtest').length > n, beforeCount, { timeout: API_WAIT_MS });
  // 保存会触发两次回测（旧闭包 + cacheKey 变化的 effect），等 worker 队列清空后再读界面
  await waitForEngineIdle(page);
  await page.waitForFunction(() => !/正在回测|回测中|加载中/.test(document.body.innerText) && /区间收益率/.test(document.body.innerText), null, { timeout: API_WAIT_MS });
  const periodText = await page.evaluate(() => document.body.innerText.match(/回测区间\s*\n?\s*([0-9-]+ 至 [0-9-]+)/)?.[1] || '');
  const [periodStart, periodEnd] = periodText.split(' 至 ');
  const dayDiff = (a, b) => Math.round((new Date(a).getTime() - new Date(b).getTime()) / 86400_000);
  const periodOk = Boolean(periodStart && periodEnd) && Math.abs(dayDiff(periodStart, ymd(start))) <= 7 && Math.abs(dayDiff(periodEnd, ymd(today))) <= 7;
  const lastBacktest = await page.evaluate(() => window.__griderE2E.requests.filter((r) => r.path === '/api/grid/backtest').slice(-1)[0] || null);
  check('3', '自定义回测区间（today−120d → today）后指标刷新', lastBacktest && lastBacktest.status === 200 && periodOk,
    `请求区间=${ymd(start)}→${ymd(today)} 实际回测区间=${periodText}（首/末交易日允差 7 天） · POST /api/grid/backtest status=${lastBacktest?.status} ${lastBacktest?.ms} ms · 之前指标片段=${beforeMetrics.replace(/\s+/g, ' ').slice(0, 60)}`);

  const exportBtn = page.getByRole('button', { name: /导出/ }).first();
  await exportBtn.waitFor({ timeout: UI_WAIT_MS });
  const [download] = await Promise.all([
    page.waitForEvent('download', { timeout: UI_WAIT_MS }),
    exportBtn.click(),
  ]);
  const saved = path.join(ARGS.out, 'e2e-export.xlsx');
  await download.saveAs(saved);
  const buf = readFileSync(saved);
  const magic = buf.subarray(0, 2).toString('latin1');
  check('3', '导出 Excel：下载文件非空且以 PK 开头', buf.length > 0 && magic === 'PK', `suggestedFilename=${download.suggestedFilename()} bytes=${buf.length} magic=${JSON.stringify(magic)}`);
}

/**
 * @param {import('playwright').Page} page
 */
async function step4Screener(page) {
  const response = await page.goto(`${ARGS.base}screener`, { waitUntil: 'domcontentloaded' });
  await waitForBodyText(page, /网格标的筛选器/, UI_WAIT_MS);
  check('4', '直达 /grider/screener（404.html 回退）渲染筛选器', true, `HTTP ${response ? response.status() : 'n/a'} · 标题可见`);
  const t0 = Date.now();
  await page.getByRole('button', { name: '开始筛选' }).click();
  await waitWithProgress(async () => {
    const snap = await page.evaluate(() => ({ rows: document.querySelectorAll('tbody tr').length, summary: (document.body.innerText.match(/成功 \d+/) || [''])[0], loading: /评分中/.test(document.body.innerText), error: (document.body.innerText.match(/筛选失败[^\n]*/) || [''])[0] }));
    if (snap.error) throw new Error(`筛选器报错：${snap.error}`);
    return { done: !snap.loading && Boolean(snap.summary), progressKey: `${snap.loading}:${snap.rows}:${snap.summary}`, detail: snap.summary || '评分中' };
  }, { stallMs: SCREENER_CAP_MS, capMs: SCREENER_CAP_MS, what: '筛选器' });
  const elapsedS = Math.round((Date.now() - t0) / 10) / 100;
  const snap = await page.evaluate(() => ({ rows: document.querySelectorAll('tbody tr').length, summary: (document.body.innerText.match(/共 \d+ 只 · 成功 \d+ · 失败 \d+[^\n]*/) || [''])[0] }));
  const succeeded = Number((snap.summary.match(/成功 (\d+)/) || [0, 0])[1]);
  const note = elapsedS > SCREENER_PREFETCH_NOTE_S ? ' · Phase 2 prefetch candidate (>120 s)' : '';
  report.screener = { elapsedS, rows: snap.rows, succeeded, summary: snap.summary };
  check('4', `筛选完成：≥ ${MIN_SCREENER_ROWS} 行且显示「成功 N」`, snap.rows >= MIN_SCREENER_ROWS && succeeded > 0, `rows=${snap.rows} ${snap.summary} · elapsed=${elapsedS}s${note}`);
}

/**
 * @param {import('playwright').Page} page
 */
async function step4bFishBasin(page) {
  await page.goto(`${ARGS.base}fish-basin`, { waitUntil: 'domcontentloaded' });
  await waitForBodyText(page, /鱼盆模型/, UI_WAIT_MS);
  await page.waitForFunction((n) => document.querySelectorAll('tbody tr').length >= n, MIN_INDEX_ROWS, { timeout: API_WAIT_MS });
  await waitFishLoaded(page);
  const indexRows = await readFishRows(page);
  const indexYesNo = indexRows.filter((r) => r.status === 'YES' || r.status === 'NO');
  check('4b', `大盘指数：YES/NO 行数 ≥ ${MIN_INDEX_ROWS}`, indexYesNo.length >= MIN_INDEX_ROWS, `rows=${indexRows.length} yesno=${indexYesNo.length} 失败=${indexRows.filter((r) => r.status === '失败').map((r) => `${r.code}${r.name}`).join(',') || '无'}`);

  const failedRows = indexRows.filter((r) => r.status === '失败');
  const zz2000 = indexRows.find((r) => r.code === UNSUPPORTED_INDEX_CODE);
  check('4b', '仅 中证2000（932000）一行显示 失败，且 title 以「在线版暂不支持」开头',
    failedRows.length === 1 && Boolean(zz2000) && zz2000.status === '失败' && typeof zz2000.title === 'string' && zz2000.title.startsWith(UNSUPPORTED_PREFIX),
    `failed=${JSON.stringify(failedRows)} zz2000=${JSON.stringify(zz2000)}`);

  const indexNotice = await page.getByTestId('fish-basin-unsupported').textContent({ timeout: UI_WAIT_MS }).catch(() => '');
  check('4b', '大盘指数：不支持提示包含「中证2000」', indexNotice.includes(UNSUPPORTED_INDEX_NAME) && indexNotice.includes(UNSUPPORTED_PREFIX), indexNotice.trim());

  await page.getByRole('button', { name: '板块轮动' }).click();
  await page.waitForFunction((code) => !Array.from(document.querySelectorAll('tbody tr')).some((tr) => tr.textContent.includes(code)), UNSUPPORTED_INDEX_CODE, { timeout: API_WAIT_MS });
  await waitFishLoaded(page);
  const sectorRows = await readFishRows(page);
  const sectorYesNo = sectorRows.filter((r) => r.status === 'YES' || r.status === 'NO');
  check('4b', `板块轮动：YES/NO 行数 ≥ ${MIN_SECTOR_ROWS}`, sectorYesNo.length >= MIN_SECTOR_ROWS, `rows=${sectorRows.length} yesno=${sectorYesNo.length} 失败=${sectorRows.filter((r) => r.status === '失败').map((r) => `${r.code}${r.name}`).join(',') || '无'}`);

  const sectorNotice = await page.getByTestId('fish-basin-unsupported').textContent({ timeout: UI_WAIT_MS }).catch(() => '');
  const missing = SECTOR_UNSUPPORTED_NAMES.filter((n) => !sectorNotice.includes(n));
  const listed = (sectorNotice.match(/：(.+?)。/) || ['', ''])[1].split('、').filter(Boolean);
  check('4b', '板块轮动：不支持提示列出 8 个名称', missing.length === 0 && listed.length === SECTOR_UNSUPPORTED_NAMES.length, `listed=${listed.join('、')} missing=${missing.join('、') || '无'}`);

  const before = await page.evaluate(() => window.__griderE2E.fishBasinCount);
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await page.waitForFunction((n) => window.__griderE2E.fishBasinCount > n, before, { timeout: API_WAIT_MS });
  await waitForEngineIdle(page);
  await waitFishLoaded(page);
  const hooked = await page.evaluate(() => window.__griderE2E.lastFishBasin);
  const session = beijingSession();
  const realtimeCount = hooked?.payload?.data?.realtime_count;
  if (session.inSession) {
    check('4b', '刷新后 realtime_count ≥ 1（交易时段）', typeof realtimeCount === 'number' && realtimeCount >= 1, `board=${hooked?.board} realtime_count=${realtimeCount} from_cache=${hooked?.payload?.data?.from_cache} · ${session.label}`);
  } else {
    check('4b', '刷新后 realtime_count（非交易时段，跳过）', true, `非交易时段，跳过 · board=${hooked?.board} realtime_count=${realtimeCount} · ${session.label}`);
  }

  const boards = await page.evaluate(() => window.__griderE2E.fishBasin);
  const dump = {
    generatedAt: new Date().toISOString(),
    base: ARGS.base,
    beijingTime: session.label,
    inSession: session.inSession,
    realtimeAssert: session.inSession ? `executed: realtime_count=${realtimeCount}` : '非交易时段，跳过',
    refresh: { board: hooked?.board ?? null, realtime_count: realtimeCount ?? null },
    index: summarizeBoard(boards.index),
    sector: summarizeBoard(boards.sector),
  };
  writeFileSync(FISH_FILE, JSON.stringify(dump, null, 2));
  report.fishBasin = { file: FISH_FILE, index: dump.index ? { supported: dump.index.supported, total: dump.index.total, realtime_count: dump.index.realtime_count } : null, sector: dump.sector ? { supported: dump.sector.supported, total: dump.sector.total, realtime_count: dump.sector.realtime_count } : null };
  check('4b', '鱼盆两个看板结果已写入 fish-basin-live.json', Boolean(dump.index && dump.sector), `index ${dump.index?.supported}/${dump.index?.total} · sector ${dump.sector?.supported}/${dump.sector?.total} → ${FISH_FILE}`);
}

/**
 * @param {import('playwright').Page} page
 */
async function step5MaBacktestAndShare(page) {
  await page.goto(`${ARGS.base}ma-backtest`, { waitUntil: 'domcontentloaded' });
  await waitForBodyText(page, /均线策略回测/, UI_WAIT_MS);
  const codeInput = page.getByPlaceholder(/如：510300/);
  await codeInput.fill('');
  await codeInput.fill(ETF_CODE);
  await page.getByRole('button', { name: '开始回测' }).click();
  await waitForBodyText(page, /策略总收益/, API_WAIT_MS);
  await page.waitForFunction(() => /交易记录（\d+ 笔）/.test(document.body.innerText), null, { timeout: UI_WAIT_MS });
  const snap = await page.evaluate(() => ({
    text: document.body.innerText,
    tradeRows: document.querySelectorAll('tbody tr').length,
    error: (document.body.innerText.match(/回测失败[^\n]*/) || [''])[0],
  }));
  const trades = Number((snap.text.match(/交易记录（(\d+) 笔）/) || [0, 0])[1]);
  check('5', '均线回测 510300：指标与交易记录表渲染', /策略总收益/.test(snap.text) && /持有不动收益/.test(snap.text) && trades > 0 && snap.tradeRows >= trades && !snap.error, `trades=${trades} tbodyRows=${snap.tradeRows}`);

  // 分享链接：产品里 useShare 钩子未被任何组件使用（无「分享」按钮），分析页把 generateAnalysisURL 生成的
  // 分享链接写入 localStorage.analysisHistory[].url —— 这就是在线版对外分享/历史跳转使用的完整 URL。
  const share = await page.evaluate((code) => {
    try {
      const history = JSON.parse(localStorage.getItem('analysisHistory') || '[]');
      const rec = history.find((h) => h.etfCode === code) || history[0] || null;
      return rec ? rec.url : null;
    } catch (e) {
      return null;
    }
  }, ETF_CODE);
  const expected = `${ARGS.base}analysis/${ETF_CODE}?`;
  let clipboard = null;
  try {
    clipboard = await page.evaluate(async (url) => { await navigator.clipboard.writeText(url); return navigator.clipboard.readText(); }, share || '');
  } catch (e) {
    clipboard = `clipboard 不可用：${e.message}`;
  }
  check('5', `分享链接以 ${expected} 开头（含站点基路径）`, typeof share === 'string' && share.startsWith(expected), `analysisHistory.url=${share} · clipboard 往返=${clipboard === share ? '一致' : clipboard}（UI 无分享按钮，useShare 未被使用）`);
}

/**
 * @param {import('playwright').Page} page
 */
async function step7Probe(page) {
  let outcome;
  try {
    outcome = await Promise.race([
      page.evaluate(() => window.__griderEngine.probe()),
      sleep(PROBE_WAIT_MS).then(() => ({ ok: false, detail: `probe 超时 ${PROBE_WAIT_MS / 1000}s` })),
    ]);
  } catch (e) {
    outcome = { ok: false, detail: String(e && e.message ? e.message : e) };
  }
  report.probe = outcome;
  check('7', 'requests 探针（非门禁，仅记录）', true, `ok=${outcome.ok} detail=${outcome.detail}`);
}

function step6Console() {
  const consoleErrors = report.consoleErrors;
  check('6', 'pageerror 计数为 0', report.pageErrors.length === 0, report.pageErrors.length ? report.pageErrors.join(' | ').slice(0, 600) : '无未捕获异常');
  check('6', 'console error（排除允许清单）计数为 0', consoleErrors.length === 0, consoleErrors.length ? consoleErrors.map((c) => c.text).join(' | ').slice(0, 800) : `无；已忽略 ${report.consoleErrorsIgnored.length} 条允许清单内错误 ${JSON.stringify(report.consoleAllowlist)}`);
}

/**
 * 运行一个步骤；异常记为该步骤的失败断言，继续后面的步骤。
 * @param {string} step
 * @param {string} name
 * @param {() => Promise<void>} fn
 */
async function runStep(step, name, fn) {
  if (!shouldRun(step)) {
    console.log(`[SKIP] step ${step} · ${name}（--live）`);
    return;
  }
  try {
    await fn();
  } catch (e) {
    check(step, `${name} — 异常`, false, String(e && e.stack ? e.stack : e).slice(0, 900));
  }
}

async function main() {
  mkdirSync(ARGS.out, { recursive: true });
  const browser = ARGS.cdp ? await chromium.connectOverCDP(ARGS.cdp) : await chromium.launch({ headless: true });
  const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1366, height: 900 }, locale: 'zh-CN', timezoneId: 'Asia/Shanghai' });
  try {
    await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: ORIGIN });
  } catch (e) {
    report.clipboardPermission = `grantPermissions 失败：${e.message}`;
  }

  if (ARGS.cdnMode === 'local') {
    report.cdnMode = 'local-mirror (forced)';
    await installCdnMirror(context);
  } else if (ARGS.cdnMode === 'remote') {
    report.cdnMode = 'remote (forced)';
  } else {
    const probe = await probeCdnFromBrowser(context);
    if (probe.ok) {
      report.cdnMode = `remote (browser HEAD ${probe.detail})`;
    } else {
      report.cdnMode = `local-mirror (browser cannot reach ${CDN_HOST}: ${probe.detail})`;
      await installCdnMirror(context);
    }
  }
  console.log(`cdnMode=${report.cdnMode}`);

  await context.addInitScript(WORKER_HOOK);
  const page = await context.newPage();
  let cdnBytes = 0;
  page.on('requestfinished', async (req) => {
    if (!req.url().includes(CDN_HOST)) return;
    try {
      const sizes = await req.sizes();
      cdnBytes += (sizes.responseBodySize || 0) + (sizes.responseHeadersSize || 0);
    } catch (e) { /* 被 route.fulfill 的请求可能没有 sizes */ }
  });
  page.on('response', (res) => {
    const req = res.request();
    if (req.resourceType() === 'document' && res.status() === 404 && req.url().startsWith(ARGS.base)) {
      report.spaFallback404Docs.push(req.url());
    }
  });
  page.on('pageerror', (e) => report.pageErrors.push(String(e && e.message ? e.message : e).slice(0, 400)));
  page.on('console', (m) => {
    if (m.type() !== 'error') return;
    const text = m.text();
    const location = m.location();
    const entry = { text: text.slice(0, 400), url: location ? location.url : '' };
    const allowed = CONSOLE_ALLOWLIST.some((re) => re.test(text) || re.test(entry.url));
    const spaFallback = SPA_FALLBACK_404_TEXT.test(text) && report.spaFallback404Docs.includes(entry.url);
    if (spaFallback) entry.reason = 'SPA 深链接文档 404（404.html 回退）';
    (allowed || spaFallback ? report.consoleErrorsIgnored : report.consoleErrors).push(entry);
  });
  page.on('dialog', async (d) => {
    report.dialogs.push(d.message());
    await d.dismiss().catch(() => {});
  });

  await runStep('1', '首页加载与引擎就绪', () => step1Engine(page, () => cdnBytes));
  const engineReady = results.some((r) => r.step === '1' && r.pass);
  if (engineReady) {
    await runStep('2', '510300 分析与回测标签', () => step2Analysis(page));
    await runStep('3', '自定义回测区间与导出 Excel', () => step3CustomBacktestAndExport(page));
    await runStep('4', '网格标的筛选器', () => step4Screener(page));
    await runStep('4b', '鱼盆模型在线路径', () => step4bFishBasin(page));
    await runStep('5', '均线回测与分享链接', () => step5MaBacktestAndShare(page));
    await runStep('7', 'requests 探针', () => step7Probe(page));
  } else {
    check('2', '引擎未就绪，后续步骤未执行', false, '见 step 1');
  }
  step6Console();

  report.engineRequests = await page.evaluate(() => (window.__griderE2E ? window.__griderE2E.requests : [])).catch(() => []);
  report.finishedAt = new Date().toISOString();
  report.summary = { total: results.length, passed: results.filter((r) => r.pass).length, failed: results.filter((r) => !r.pass).length };
  writeFileSync(RESULT_FILE, JSON.stringify(report, null, 2));
  console.log(`\n结果：${report.summary.passed}/${report.summary.total} 通过 → ${RESULT_FILE}`);

  await context.close();
  if (!ARGS.cdp) await browser.close();
  process.exit(report.summary.failed === 0 ? 0 : 1);
}

main().catch((e) => {
  check('0', '脚本异常', false, String(e && e.stack ? e.stack : e));
  report.finishedAt = new Date().toISOString();
  report.summary = { total: results.length, passed: results.filter((r) => r.pass).length, failed: results.filter((r) => !r.pass).length };
  mkdirSync(ARGS.out, { recursive: true });
  writeFileSync(RESULT_FILE, JSON.stringify(report, null, 2));
  process.exit(1);
});
