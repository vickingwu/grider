/**
 * grider 在线引擎 classic worker（非 ES module：需要 importScripts 加载 pyodide.js 与无 CORS 的 JSONP 数据源脚本）。
 *
 * 由 scripts/postbuild-pages.mjs 原样复制到 dist/engine/，仅 Pages 构建包含；http 构建的 dist 不含本文件。
 * 全部配置由主线程的 init 消息传入（indexURL / packages / wheelUrls / zipUrl），本文件不含版本号字面量。
 *
 * 协议（design.md §6.2.2）：
 *   ← init {indexURL, packages[], wheelUrls[], zipUrl, buildInfo}
 *   → stage {stage, detail, bytesSoFar}   → ready {version, python, pandas, elapsedMs, transferBytes}
 *   → init-error {stage, message, hint}
 *   ← request {id, method, path, query, body}   → response {id, status, contentType, body} | error {id, message}
 *   ← probe-requests                            → probe-result {ok, detail}
 *   Python 取数桥（registerJsModule 'grider_bridge'）：httpGetBytes(url, timeoutMs, ttl) / loadScriptVars(url, namesJson, ttl)
 *   / evictCache(url)（空页重试前驱逐 Map + IndexedDB 中该 URL 的缓存）
 *
 * 整个文件包在严格模式 IIFE 内：importScripts 引入的供应商 `var` 全局不会与本地绑定冲突；绝不 delete 全局。
 */
(() => {
  'use strict';

  const INDEX_URL_PATTERN = /^https:\/\/cdn\.jsdelivr\.net\/pyodide\/v\d+\.\d+\.\d+\/full\/$/;
  const SCRIPT_HOSTS = new Set(['fund.eastmoney.com', 'quotes.sina.cn', 'stock2.finance.sina.com.cn']);
  const ALLOWED_METHODS = new Set(['GET', 'POST']);
  const MAX_PATH_LEN = 2048;
  const MAX_QUERY_LEN = 4096;
  const MAX_BODY_LEN = 1048576;
  const API_PREFIX = '/api/';
  const WHEELS_DIR = '/wheels';
  const BACKEND_DIR = '/grider';
  const PROGRESS_INTERVAL_MS = 2000;
  const CACHE_DB_NAME = 'grider-engine-cache';
  const CACHE_STORE = 'responses';
  const CACHE_CAP_BYTES = 64 * 1024 * 1024;
  const VARS_KEY_PREFIX = 'vars:';
  const PROBE_URL = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh510300,day,,,1,qfq';
  const PROBE_TIMEOUT_MS = 15000;
  const HINT = '请确认网络可访问 cdn.jsdelivr.net 后点击「重试」；或改用本地版（README「快速开始」）。';

  let pyodide = null;
  let dispatchJson = null;
  let currentStage = null;
  let progressTimer = null;
  let initStartedAt = 0;

  // ---------- 进度 ----------

  /**
   * 累计资源传输字节（jsdelivr 发送 Timing-Allow-Origin: *，否则为 0）。
   * @returns {number}
   */
  function bytesSoFar() {
    let total = 0;
    for (const entry of performance.getEntriesByType('resource')) total += entry.transferSize || 0;
    return total;
  }

  function postStage(detail) {
    self.postMessage({ type: 'stage', stage: currentStage, detail: detail || '', bytesSoFar: bytesSoFar() });
  }

  function setStage(stage, detail) {
    currentStage = stage;
    postStage(detail);
    clearInterval(progressTimer);
    progressTimer = setInterval(() => postStage(detail), PROGRESS_INTERVAL_MS);
  }

  function stopProgress() {
    clearInterval(progressTimer);
    progressTimer = null;
  }

  // ---------- 浏览器侧缓存：Map + IndexedDB ----------

  const memCache = new Map(); // key -> {bytes|text, expires, size}
  let memCacheBytes = 0;
  let idb = null;
  let idbWarned = false;

  function warnIdbOnce(reason) {
    if (idbWarned) return;
    idbWarned = true;
    console.warn('[engine-cache] IndexedDB 不可用，仅使用内存缓存:', reason);
  }

  function entrySize(entry) {
    return entry.bytes ? entry.bytes.byteLength : (entry.text ? entry.text.length * 2 : 0);
  }

  function memPut(key, entry) {
    const existing = memCache.get(key);
    if (existing) memCacheBytes -= existing.size;
    entry.size = entrySize(entry);
    memCache.set(key, entry);
    memCacheBytes += entry.size;
    // 64 MB 上限，按插入顺序（最旧）淘汰
    for (const [oldKey, old] of memCache) {
      if (memCacheBytes <= CACHE_CAP_BYTES) break;
      memCache.delete(oldKey);
      memCacheBytes -= old.size;
    }
  }

  function openIdb() {
    return new Promise((resolve) => {
      if (typeof indexedDB === 'undefined') {
        warnIdbOnce('indexedDB undefined');
        resolve(null);
        return;
      }
      let req;
      try {
        req = indexedDB.open(CACHE_DB_NAME, 1);
      } catch (e) {
        warnIdbOnce(e && e.message);
        resolve(null);
        return;
      }
      req.onupgradeneeded = () => {
        if (!req.result.objectStoreNames.contains(CACHE_STORE)) req.result.createObjectStore(CACHE_STORE, { keyPath: 'key' });
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => {
        warnIdbOnce(req.error && req.error.message);
        resolve(null);
      };
      req.onblocked = () => resolve(null);
    });
  }

  /** 从 IndexedDB 把未过期条目灌入 Map，过期条目删除；任何失败只告警一次 */
  function hydrateCache() {
    return openIdb().then((db) => {
      idb = db;
      if (!db) return;
      return new Promise((resolve) => {
        let tx;
        try {
          tx = db.transaction(CACHE_STORE, 'readwrite');
        } catch (e) {
          warnIdbOnce(e && e.message);
          resolve();
          return;
        }
        const store = tx.objectStore(CACHE_STORE);
        const cursorReq = store.openCursor();
        const now = Date.now();
        cursorReq.onsuccess = () => {
          const cursor = cursorReq.result;
          if (!cursor) return;
          const value = cursor.value || {};
          const valid = typeof value.key === 'string' && Number.isFinite(value.expires)
            && (value.bytes instanceof Uint8Array || typeof value.text === 'string');
          if (!valid || value.expires <= now) {
            cursor.delete();
          } else {
            memPut(value.key, { bytes: value.bytes, text: value.text, expires: value.expires });
          }
          cursor.continue();
        };
        tx.oncomplete = () => resolve();
        tx.onerror = () => {
          warnIdbOnce(tx.error && tx.error.message);
          resolve();
        };
        tx.onabort = () => resolve();
      });
    });
  }

  /**
   * 写入缓存：Map 立即生效，IndexedDB 异步写（失败只告警一次）。
   * @param {string} key
   * @param {Uint8Array|string} value
   * @param {number} ttlSeconds
   */
  function cachePut(key, value, ttlSeconds) {
    const expires = Date.now() + ttlSeconds * 1000;
    const entry = typeof value === 'string' ? { text: value, expires } : { bytes: value, expires };
    memPut(key, entry);
    if (!idb) return;
    try {
      const tx = idb.transaction(CACHE_STORE, 'readwrite');
      tx.objectStore(CACHE_STORE).put({ key, expires, bytes: entry.bytes, text: entry.text });
      tx.onerror = () => warnIdbOnce(tx.error && tx.error.message);
    } catch (e) {
      warnIdbOnce(e && e.message);
    }
  }

  function cacheGet(key) {
    const hit = memCache.get(key);
    if (hit && hit.expires > Date.now()) return hit;
    if (hit) {
      memCache.delete(key);
      memCacheBytes -= hit.size;
    }
    return null;
  }

  // ---------- Python 取数桥（同步） ----------

  const bridge = {
    /**
     * 同步 GET；ttlSeconds 为 0 时不读也不写缓存（实时行情永远走网络）。
     * @param {string} url
     * @param {number} timeoutMs
     * @param {number} ttlSeconds
     * @returns {Uint8Array}
     */
    httpGetBytes(url, timeoutMs, ttlSeconds) {
      const ttl = Number(ttlSeconds) || 0;
      if (ttl > 0) {
        const hit = cacheGet(url);
        if (hit && hit.bytes) return hit.bytes;
      }
      const xhr = new XMLHttpRequest();
      xhr.open('GET', url, false);
      xhr.responseType = 'arraybuffer';
      xhr.timeout = Number(timeoutMs) || 0;
      xhr.send(); // 网络错误 / CORS 失败 / 超时时抛出
      if (xhr.status !== 200) throw new Error(`HTTP ${xhr.status} ${url}`);
      const bytes = new Uint8Array(xhr.response);
      if (ttl > 0) cachePut(url, bytes, ttl);
      return bytes;
    },

    /**
     * 驱逐某个 URL 的缓存条目（Map 立即删除，IndexedDB 异步删除）。Python 侧在腾讯/东财空页重试前调用，
     * 否则限流期被按 TTL 缓存的 200 空页会让「刷新」在最长 1 小时内始终读到空数据。
     * @param {string} url
     * @returns {boolean}
     */
    evictCache(url) {
      const hit = memCache.get(url);
      if (hit) {
        memCache.delete(url);
        memCacheBytes -= hit.size;
      }
      if (idb) {
        try {
          const tx = idb.transaction(CACHE_STORE, 'readwrite');
          tx.objectStore(CACHE_STORE).delete(url);
          tx.onerror = () => warnIdbOnce(tx.error && tx.error.message);
        } catch (e) {
          warnIdbOnce(e && e.message);
        }
      }
      return true;
    },

    /**
     * 通过 importScripts 执行无 CORS 的 JSONP/变量脚本并读取指定全局变量，返回 JSON 字符串。
     * @param {string} url
     * @param {string} namesJson - JSON 数组字符串（Python 侧 json.dumps）
     * @param {number} ttlSeconds
     * @returns {string}
     */
    loadScriptVars(url, namesJson, ttlSeconds) {
      const host = new URL(url).hostname;
      if (!SCRIPT_HOSTS.has(host)) throw new Error(`script host not allowed: ${host}`);
      const names = JSON.parse(namesJson);
      if (!Array.isArray(names) || !names.every((n) => typeof n === 'string' && n)) throw new Error('names 必须是非空字符串数组');
      const ttl = Number(ttlSeconds) || 0;
      const key = VARS_KEY_PREFIX + url;
      if (ttl > 0) {
        const hit = cacheGet(key);
        if (hit && typeof hit.text === 'string') return hit.text;
      }
      for (const n of names) self[n] = undefined; // var 全局不可 delete，但可写
      importScripts(url); // 同步；404 / 网络 / 语法错误时抛出
      const out = {};
      for (const n of names) {
        if (self[n] === undefined) throw new Error(`变量未定义: ${n} ${url}`);
        out[n] = self[n];
        self[n] = undefined;
      }
      const text = JSON.stringify(out);
      if (ttl > 0) cachePut(key, text, ttl);
      return text;
    },
  };

  // ---------- init 校验与执行 ----------

  function isSameOrigin(u) {
    try {
      return new URL(u, self.location.href).origin === self.location.origin;
    } catch {
      return false;
    }
  }

  /**
   * @param {object} msg
   * @returns {string|null} 错误描述；合法时为 null
   */
  function validateInit(msg) {
    if (typeof msg.indexURL !== 'string' || !INDEX_URL_PATTERN.test(msg.indexURL)) return 'indexURL 非法';
    if (!Array.isArray(msg.packages) || !msg.packages.length || !msg.packages.every((p) => typeof p === 'string' && p)) return 'packages 必须为非空字符串数组';
    if (!Array.isArray(msg.wheelUrls) || !msg.wheelUrls.length || !msg.wheelUrls.every((u) => typeof u === 'string' && isSameOrigin(u))) return 'wheelUrls 必须为同源地址';
    if (typeof msg.zipUrl !== 'string' || !isSameOrigin(msg.zipUrl)) return 'zipUrl 必须为同源地址';
    return null;
  }

  function postInitError(stage, message) {
    stopProgress();
    console.error(`[engine] 初始化失败（${stage}）: ${message}`);
    self.postMessage({ type: 'init-error', stage, message, hint: HINT });
  }

  async function fetchBytes(url) {
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status} ${url}`);
    return new Uint8Array(await resp.arrayBuffer());
  }

  function wheelName(url) {
    return decodeURIComponent(new URL(url, self.location.href).pathname.split('/').pop());
  }

  /**
   * 取 Python 异常的最后一行（traceback 已通过 stderr → console.warn 输出）。
   * @param {*} e
   * @returns {string}
   */
  function lastLine(e) {
    const text = String((e && e.message) || e).trim();
    const lines = text.split('\n').filter((l) => l.trim());
    return lines.length ? lines[lines.length - 1].trim() : text;
  }

  async function runInit(msg) {
    initStartedAt = performance.now();
    const configError = validateInit(msg);
    if (configError) {
      postInitError('config', configError);
      return;
    }
    await hydrateCache();

    setStage('runtime', 'pyodide.js');
    try {
      importScripts(msg.indexURL + 'pyodide.js');
      pyodide = await self.loadPyodide({
        indexURL: msg.indexURL,
        stdout: (m) => console.log('[py]', m),
        stderr: (m) => console.warn('[py]', m),
      });
    } catch (e) {
      postInitError('runtime', lastLine(e));
      return;
    }

    setStage('packages', msg.packages.join(', '));
    try {
      await pyodide.loadPackage(msg.packages);
    } catch (e) {
      postInitError('packages', lastLine(e));
      return;
    }

    setStage('wheels', '');
    try {
      pyodide.FS.mkdirTree(WHEELS_DIR);
      const emfsPaths = [];
      for (const url of msg.wheelUrls) {
        const name = wheelName(url);
        postStage(name);
        pyodide.FS.writeFile(`${WHEELS_DIR}/${name}`, await fetchBytes(url));
        emfsPaths.push(`emfs:${WHEELS_DIR}/${name}`);
      }
      const micropip = pyodide.pyimport('micropip');
      const pyPaths = pyodide.toPy(emfsPaths); // 转为 Python list，避免 JsProxy 在 micropip 内部的类型判断分歧
      try {
        await micropip.install.callKwargs(pyPaths, { deps: false });
      } finally {
        pyPaths.destroy();
        micropip.destroy();
      }
    } catch (e) {
      postInitError('wheels', lastLine(e));
      return;
    }

    setStage('backend', 'backend.zip');
    try {
      pyodide.unpackArchive(await fetchBytes(msg.zipUrl), 'zip', { extractDir: BACKEND_DIR });
      pyodide.runPython(`import sys\nif ${JSON.stringify(BACKEND_DIR)} not in sys.path:\n    sys.path.insert(0, ${JSON.stringify(BACKEND_DIR)})\n`);
    } catch (e) {
      postInitError('backend', lastLine(e));
      return;
    }

    setStage('bootstrap', '');
    let info;
    try {
      pyodide.registerJsModule('grider_bridge', bridge);
      const infoJson = await pyodide.runPythonAsync(
        'from app.browser import bootstrap\nimport grider_bridge\nbootstrap.init(grider_bridge)\n',
      );
      info = JSON.parse(infoJson);
      dispatchJson = pyodide.runPython('from app.browser.dispatch import dispatch_json\ndispatch_json');
    } catch (e) {
      postInitError('bootstrap', lastLine(e));
      return;
    }

    stopProgress();
    currentStage = 'ready';
    self.postMessage({
      type: 'ready',
      version: info.version,
      python: info.python,
      pandas: info.pandas,
      elapsedMs: Math.round(performance.now() - initStartedAt),
      transferBytes: bytesSoFar(),
    });
  }

  // ---------- request ----------

  /**
   * 调用 Python 之前的协议级校验（design.md §9）。
   * @param {object} msg
   * @returns {string|null}
   */
  function validateRequest(msg) {
    if (typeof msg.id !== 'string' || !msg.id) return 'id 必须为字符串';
    if (!ALLOWED_METHODS.has(msg.method)) return 'method 仅支持 GET/POST';
    if (typeof msg.path !== 'string' || !msg.path.startsWith(API_PREFIX) || msg.path.length > MAX_PATH_LEN || msg.path.includes('..')) return 'path 非法';
    if (msg.query != null && (typeof msg.query !== 'string' || msg.query.length > MAX_QUERY_LEN)) return 'query 非法';
    if (msg.body != null && (typeof msg.body !== 'string' || msg.body.length > MAX_BODY_LEN)) return 'body 非法';
    return null;
  }

  function handleRequest(msg) {
    const id = typeof msg.id === 'string' ? msg.id : null;
    const problem = validateRequest(msg);
    if (problem) {
      self.postMessage({ type: 'error', id, message: problem });
      return;
    }
    if (!dispatchJson) {
      self.postMessage({ type: 'error', id, message: '在线引擎尚未就绪' });
      return;
    }
    try {
      // Pyodide 0.29 把 JS null 转为 JsNull（非 None）：无请求体必须传 undefined
      const result = JSON.parse(dispatchJson(msg.method, msg.path, msg.query || '', msg.body == null ? undefined : msg.body));
      self.postMessage({ type: 'response', id, status: result.status, contentType: result.content_type, body: result.body });
    } catch (e) {
      console.error('[engine] dispatch 异常', e);
      self.postMessage({ type: 'error', id, message: lastLine(e) });
    }
  }

  // ---------- probe-requests（仅 E2E） ----------

  async function handleProbe() {
    if (!pyodide) {
      self.postMessage({ type: 'probe-result', ok: false, detail: '引擎未初始化' });
      return;
    }
    try {
      await pyodide.loadPackage('requests');
      const detail = await pyodide.runPythonAsync(
        `import requests\nr = requests.get(${JSON.stringify(PROBE_URL)}, timeout=${PROBE_TIMEOUT_MS / 1000})\nf"{r.status_code} {len(r.content)} bytes"\n`,
      );
      self.postMessage({ type: 'probe-result', ok: true, detail: String(detail) });
    } catch (e) {
      self.postMessage({ type: 'probe-result', ok: false, detail: lastLine(e) });
    }
  }

  // ---------- 消息入口 ----------

  self.onmessage = (event) => {
    const msg = event.data || {};
    switch (msg.type) {
      case 'init':
        runInit(msg).catch((e) => postInitError(currentStage || 'runtime', lastLine(e)));
        return;
      case 'request':
        handleRequest(msg);
        return;
      case 'probe-requests':
        handleProbe();
        return;
      default:
        console.warn('[engine] 未知消息类型', msg.type);
    }
  };

  self.onerror = (event) => {
    console.error('[engine] worker 未捕获异常', (event && event.message) || event);
  };
})();
