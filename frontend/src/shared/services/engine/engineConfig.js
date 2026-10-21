/**
 * 在线引擎（Pyodide）配置常量。
 *
 * 本文件是 src/、engine/、scripts/、tests/、.github/ 之下唯一允许出现 Pyodide 版本号字面量的地方，
 * 由 tests/engine/version-pin.test.mjs 强制（package.json 的 devDependencies.pyodide 必须与之相等）。
 * 本模块必须保持可被 Node 直接 import（无 import.meta.env、无浏览器 API），供构建脚本与测试复用。
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
 */
export const PYODIDE_PACKAGES = ['micropip', 'jinja2', 'markupsafe', 'click', 'numpy', 'pandas'];

/** 仅 Flask 栈所需的官方包（探针 P1 阶段使用；P2 再追加 numpy/pandas） */
export const PYODIDE_FLASK_PACKAGES = ['micropip', 'jinja2', 'markupsafe', 'click'];
