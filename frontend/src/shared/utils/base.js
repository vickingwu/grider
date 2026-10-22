/**
 * 站点基路径工具（纯函数，可被 node:test 直接 import）。
 * http 构建 BASE_URL 为 '/'；Pages 构建为 '/grider/'。
 */

/**
 * 当前构建的基路径（以 '/' 结尾）；非 Vite 环境回退为 '/'。
 * @returns {string}
 */
export const getBaseUrl = () =>
  (typeof import.meta !== "undefined" && import.meta.env && import.meta.env.BASE_URL) || "/";

/**
 * 把站内路径拼到基路径之后，保证恰好一个 '/' 分隔。
 * @param {string} base - 基路径，如 '/' 或 '/grider/'
 * @param {string} path - 站内路径，如 'analysis/x' 或 '/analysis/x'
 * @returns {string} 如 '/grider/analysis/x'
 */
export const joinBase = (base, path) =>
  `${String(base || "/").replace(/\/$/, "")}/${String(path ?? "").replace(/^\//, "")}`;
