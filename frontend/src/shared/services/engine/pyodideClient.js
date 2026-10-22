/**
 * 在线引擎客户端：管理 Pyodide classic worker 的生命周期、请求队列、超时与状态广播。
 *
 * 仅在 __GRIDER_ENGINE__ === 'pyodide' 的构建中被 transport.js 动态导入；http 构建不包含本模块。
 * 对外：ensureEngine()（共享初始化 Promise）、engineFetch(url, init)（Response 形状）、
 * engineEvents（'statuschange' 事件）、getEngineState()；并在 window.__griderEngine 暴露状态与 probe() 供 E2E。
 */
import {
  ENGINE_NOT_READY_PREFIX,
  INIT_CAP_MS,
  INIT_STALL_MS,
  INIT_TIMEOUT_MESSAGE,
  PYODIDE_INDEX_URL,
  PYODIDE_PACKAGES,
  REQUEST_TIMEOUT_MESSAGE,
  REQUEST_TIMEOUT_MS,
  SCREENER_PATH_PREFIX,
  SCREENER_TIMEOUT_MS,
  WORKER_CRASHED_MESSAGE,
  engineAssetUrls,
} from './engineConfig.js';

/** EngineGate 与测试订阅的状态事件源（事件名 'statuschange'，detail 为 getEngineState() 快照） */
export const engineEvents = new EventTarget();

const STATUS_CHANGE_EVENT = 'statuschange';
const BUILD_SHA = typeof __GRIDER_BUILD_SHA__ === 'string' ? __GRIDER_BUILD_SHA__ : 'dev';
const ASSETS = engineAssetUrls(import.meta.env.BASE_URL, BUILD_SHA);
const JSON_CONTENT_TYPE = 'application/json';
const REQUEST_FAILED_MESSAGE = '在线引擎请求失败';

const state = {
  status: 'loading',
  stage: null,
  detail: '',
  elapsedMs: 0,
  transferBytes: 0,
  error: null,
  info: null,
};

let worker = null;
let enginePromise = null;
let initResolve = null;
let initReject = null;
let initStartedAt = 0;
let stallTimer = null;
let capTimer = null;
let requestSeq = 0;
let active = null;
let probeWaiter = null;
const queue = [];

/**
 * @returns {{status: string, stage: string|null, detail: string, elapsedMs: number, transferBytes: number, error: string|null, info: object|null}}
 */
export function getEngineState() {
  return { ...state };
}

function setState(patch) {
  Object.assign(state, patch);
  engineEvents.dispatchEvent(new CustomEvent(STATUS_CHANGE_EVENT, { detail: getEngineState() }));
}

function elapsedSinceInit() {
  return Math.round(performance.now() - initStartedAt);
}

function clearInitTimers() {
  clearTimeout(stallTimer);
  clearTimeout(capTimer);
  stallTimer = null;
  capTimer = null;
}

function terminateWorker() {
  if (worker) {
    worker.terminate();
    worker = null;
  }
  clearInitTimers();
}

/**
 * 拒绝队列中与进行中的全部请求。
 * @param {Error} error
 */
function rejectAllRequests(error) {
  if (active) {
    clearTimeout(active.timer);
    active.reject(error);
    active = null;
  }
  while (queue.length) queue.shift().reject(error);
  if (probeWaiter) {
    probeWaiter.reject(error);
    probeWaiter = null;
  }
}

/**
 * 终止 worker 并重置 ensureEngine，使下一次调用重新初始化（IndexedDB 缓存保留，资产走 HTTP 缓存）。
 * @param {Error} error
 */
function resetEngine(error) {
  terminateWorker();
  rejectAllRequests(error);
  enginePromise = null;
  initResolve = null;
  initReject = null;
  ensureEngine().catch(() => {});
}

/**
 * 初始化失败：终止 worker、进入 error 状态、拒绝初始化 Promise 与所有挂起请求。
 * @param {string} stage
 * @param {string} message
 */
function failInit(stage, message) {
  terminateWorker();
  setState({ status: 'error', stage, error: message, elapsedMs: elapsedSinceInit() });
  const error = new Error(message);
  error.stage = stage;
  if (initReject) initReject(error);
  initReject = null;
  initResolve = null;
  rejectAllRequests(new Error(ENGINE_NOT_READY_PREFIX + message));
}

function armStallTimer(stage) {
  clearTimeout(stallTimer);
  stallTimer = setTimeout(() => failInit(stage, INIT_TIMEOUT_MESSAGE), INIT_STALL_MS);
}

/**
 * 请求超时时长：筛选器类接口 900 s，其余 120 s。
 * @param {string} path
 * @returns {number}
 */
function timeoutFor(path) {
  return path.startsWith(SCREENER_PATH_PREFIX) ? SCREENER_TIMEOUT_MS : REQUEST_TIMEOUT_MS;
}

/** 队列泵：worker 内 Python 串行执行，客户端也一次只发一个请求，超时从真正发出时计时 */
function pump() {
  if (active || !queue.length || !worker || state.status !== 'ready') return;
  active = queue.shift();
  const { id, method, path, query, body } = active.message;
  active.timer = setTimeout(() => {
    console.warn(`[engine] 请求超时 ${method} ${path}`);
    resetEngine(new Error(REQUEST_TIMEOUT_MESSAGE));
  }, timeoutFor(path));
  worker.postMessage({ type: 'request', id, method, path, query, body });
}

function finishActive(id, handler) {
  if (!active || active.message.id !== id) return;
  clearTimeout(active.timer);
  const done = active;
  active = null;
  handler(done);
  pump();
}

function onStage(msg) {
  const progressed = msg.stage !== state.stage || msg.bytesSoFar !== state.transferBytes;
  if (progressed) armStallTimer(msg.stage);
  setState({
    status: 'loading',
    stage: msg.stage,
    detail: msg.detail || '',
    transferBytes: msg.bytesSoFar || 0,
    elapsedMs: elapsedSinceInit(),
  });
}

function onReady(msg) {
  clearInitTimers();
  setState({
    status: 'ready',
    stage: 'ready',
    error: null,
    info: msg,
    transferBytes: msg.transferBytes || state.transferBytes,
    elapsedMs: msg.elapsedMs || elapsedSinceInit(),
  });
  if (initResolve) initResolve(msg);
  initResolve = null;
  initReject = null;
  pump();
}

/**
 * 处理 worker 消息。
 * @param {MessageEvent} event
 */
function handleMessage(event) {
  const msg = event.data || {};
  switch (msg.type) {
    case 'stage':
      onStage(msg);
      return;
    case 'ready':
      onReady(msg);
      return;
    case 'init-error':
      console.error(`[engine] 初始化失败（${msg.stage}）: ${msg.message}`);
      failInit(msg.stage, msg.message);
      return;
    case 'response':
      finishActive(msg.id, (req) => req.resolve(msg));
      return;
    case 'error':
      finishActive(msg.id, (req) => req.reject(new Error(msg.message || REQUEST_FAILED_MESSAGE)));
      return;
    case 'probe-result':
      if (probeWaiter) {
        probeWaiter.resolve({ ok: Boolean(msg.ok), detail: msg.detail });
        probeWaiter = null;
      }
      return;
    default:
      console.warn('[engine] 未知消息', msg);
  }
}

function handleWorkerError(event) {
  const message = event.message || WORKER_CRASHED_MESSAGE;
  console.error('[engine] worker 异常', message);
  if (state.status === 'ready') {
    resetEngine(new Error(WORKER_CRASHED_MESSAGE));
    return;
  }
  failInit(state.stage || 'runtime', message);
}

/**
 * 启动（或复用）引擎初始化；返回共享 Promise。初始化失败后不会自动重试（横幅提供「重试」= reload）。
 * @returns {Promise<object>} ready 消息
 */
export function ensureEngine() {
  if (enginePromise) return enginePromise;
  enginePromise = new Promise((resolve, reject) => {
    initResolve = resolve;
    initReject = reject;
    initStartedAt = performance.now();
    setState({ status: 'loading', stage: null, detail: '', error: null, elapsedMs: 0, transferBytes: 0 });
    try {
      worker = new Worker(ASSETS.workerUrl);
    } catch (e) {
      failInit('runtime', e.message || String(e));
      return;
    }
    worker.onmessage = handleMessage;
    worker.onerror = handleWorkerError;
    armStallTimer('runtime');
    capTimer = setTimeout(() => failInit(state.stage || 'runtime', INIT_TIMEOUT_MESSAGE), INIT_CAP_MS);
    worker.postMessage({
      type: 'init',
      indexURL: PYODIDE_INDEX_URL,
      packages: PYODIDE_PACKAGES,
      wheelUrls: ASSETS.wheelUrls,
      zipUrl: ASSETS.zipUrl,
      buildInfo: { sha: BUILD_SHA },
    });
  });
  return enginePromise;
}

/**
 * 把 worker 的 response 消息包装为 fetch Response 的最小子集。
 * @param {{status: number, contentType: string, body: string}} msg
 */
function toResponse(msg) {
  const contentType = msg.contentType || JSON_CONTENT_TYPE;
  const body = msg.body ?? '';
  return {
    ok: msg.status < 400,
    status: msg.status,
    headers: { get: (name) => (String(name).toLowerCase() === 'content-type' ? contentType : null) },
    json: async () => JSON.parse(body),
    text: async () => body,
  };
}

/**
 * fetch 的在线引擎替代：url 可为相对路径（/api/...）；init.method 默认 GET，body 需为字符串或空。
 * @param {string} url
 * @param {RequestInit} [init]
 * @returns {Promise<{ok: boolean, status: number, headers: {get: Function}, json: Function, text: Function}>}
 */
export async function engineFetch(url, init) {
  try {
    await ensureEngine();
  } catch (e) {
    throw new Error(ENGINE_NOT_READY_PREFIX + (e.message || String(e)));
  }
  const u = new URL(url, location.origin);
  const rawBody = init?.body;
  const message = {
    id: `r${++requestSeq}`,
    method: (init?.method || 'GET').toUpperCase(),
    path: u.pathname,
    query: u.search.slice(1),
    body: rawBody == null ? null : String(rawBody),
  };
  const result = await new Promise((resolve, reject) => {
    queue.push({ message, resolve, reject, timer: null });
    pump();
  });
  return toResponse(result);
}

/**
 * E2E 探针：让 worker 加载 requests 包并访问一个腾讯接口，返回 {ok, detail}。
 * @returns {Promise<{ok: boolean, detail: string}>}
 */
export async function probe() {
  await ensureEngine();
  if (probeWaiter) return probeWaiter.promise;
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  probeWaiter = { promise, resolve, reject };
  worker.postMessage({ type: 'probe-requests' });
  return promise;
}

if (typeof window !== 'undefined') {
  window.__griderEngine = {
    get status() { return state.status; },
    get stage() { return state.stage; },
    get elapsedMs() { return state.elapsedMs; },
    get transferBytes() { return state.transferBytes; },
    get error() { return state.error; },
    get info() { return state.info; },
    probe,
  };
}
