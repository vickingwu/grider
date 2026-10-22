/**
 * 在线引擎（Pyodide）配置常量。
 *
 * 本文件是 src/、engine/、scripts/、tests/、.github/ 之下唯一允许出现 Pyodide 版本号字面量的地方，
 * 由 tests/engine/version-pin.test.mjs 强制（package.json 的 devDependencies.pyodide 必须与之相等）。
 * 本模块必须保持可被 Node 直接 import（无 import.meta.env、无浏览器 API），供构建脚本与测试复用；
 * 因此与 BASE_URL 相关的路径通过 engineAssetUrls(baseUrl) 由调用方传入基路径生成。
 */

/** Pyodide 运行时版本（与 package.json 的 pyodide devDependency 一致） */
export const PYODIDE_VERSION = '0.29.5';

/** Pyodide 运行时 CDN 根地址（浏览器 worker 使用；Node 侧使用 npm 包自带的 indexURL） */
export const PYODIDE_INDEX_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

/**
 * 通过 pyodide.loadPackage 预装的 Pyodide 官方包（来源：0.29.5 lock）。
 * 其中 micropip/jinja2/markupsafe/click 是 Flask wheel 的依赖；numpy/pandas 是后端算法依赖。
 * 若探针（scripts/probe-flask-pyodide.mjs）发现标准库模块缺失（ssl/hashlib/lzma/sqlite3/pydecimal 等
 * 在 Pyodide 中是独立包），应在此处追加，而不是在 Python 侧打 sys.modules 桩。
 * 探针结论（EVID\probe-flask-pyodide.txt）：P1/P2 PASS，无需追加标准库包。
 */
export const PYODIDE_PACKAGES = ['micropip', 'jinja2', 'markupsafe', 'click', 'numpy', 'pandas'];

/** 仅 Flask 栈所需的官方包（探针 P1 阶段使用；P2 再追加 numpy/pandas） */
export const PYODIDE_FLASK_PACKAGES = ['micropip', 'jinja2', 'markupsafe', 'click'];

/**
 * 随 Pages 构建一同发布的纯 Python wheel 文件名（必须与 scripts/wheels.lock.json 的 filename 一致；
 * scripts/postbuild-pages.mjs 会校验 dist/engine/wheels 与此列表完全相同）。
 */
export const WHEEL_FILES = [
  'flask-3.1.2-py3-none-any.whl',
  'werkzeug-3.1.3-py3-none-any.whl',
  'itsdangerous-2.2.0-py3-none-any.whl',
  'blinker-1.9.0-py3-none-any.whl',
];

/** dist/ 之下引擎资源的相对目录 */
export const ENGINE_DIR = 'engine';

/**
 * 根据站点基路径（import.meta.env.BASE_URL，如 '/' 或 '/grider/'）生成引擎资源 URL。
 * @param {string} baseUrl - 以 '/' 结尾的基路径
 * @param {string} buildSha - 构建 SHA，用作缓存失效参数（worker/zip/wheel 每次部署都更新）
 * @returns {{workerUrl: string, zipUrl: string, manifestUrl: string, wheelUrls: string[]}}
 */
export const engineAssetUrls = (baseUrl, buildSha) => {
  const root = `${String(baseUrl || '/').replace(/\/$/, '')}/${ENGINE_DIR}/`;
  const version = encodeURIComponent(buildSha || 'dev');
  return {
    workerUrl: `${root}pyodide.worker.js?v=${version}`,
    zipUrl: `${root}backend.zip?v=${version}`,
    manifestUrl: `${root}manifest.json?v=${version}`,
    wheelUrls: WHEEL_FILES.map((name) => `${root}wheels/${name}?v=${version}`),
  };
};

/** 普通请求超时（毫秒） */
export const REQUEST_TIMEOUT_MS = 120_000;

/** 筛选器类请求超时（毫秒）：路径以 /api/screener 开头（含 /api/screener/ma、/api/screener/fish-basin） */
export const SCREENER_TIMEOUT_MS = 900_000;

/** 判定为筛选器请求的路径前缀 */
export const SCREENER_PATH_PREFIX = '/api/screener';

/** 初始化：连续无进度（无 stage / bytesSoFar 变化）判定失败的时长（毫秒） */
export const INIT_STALL_MS = 180_000;

/** 初始化：硬上限（毫秒） */
export const INIT_CAP_MS = 900_000;

/** 初始化阶段顺序（与 worker 的 stage 消息一致，n/5 的分母即此数组长度） */
export const INIT_STAGES = ['runtime', 'packages', 'wheels', 'backend', 'bootstrap'];

/** 各阶段中文标签（EngineGate 文案「阶段 n/5：…」） */
export const STAGE_LABELS = {
  runtime: '下载 Python 运行时',
  packages: '下载 numpy·pandas',
  wheels: '安装 Flask',
  backend: '加载分析引擎',
  bootstrap: '初始化',
};

/** 请求超时时抛给页面的错误文案 */
export const REQUEST_TIMEOUT_MESSAGE = '在线引擎响应超时，请刷新页面重试';

/** 初始化超时时的错误文案 */
export const INIT_TIMEOUT_MESSAGE = '加载超时';

/** worker 崩溃（onerror）时抛给页面的错误文案 */
export const WORKER_CRASHED_MESSAGE = '在线引擎已停止，请刷新页面重试';

/** 初始化失败后 API 调用的错误前缀 */
export const ENGINE_NOT_READY_PREFIX = '在线运行环境未就绪：';
