/**
 * 传输层：按构建期常量 __GRIDER_ENGINE__ 选择 fetch（http 引擎）或 Pyodide worker（在线引擎）。
 *
 * 与字符串字面量比较，使 http 构建中 engine 分支及其动态 import 被整体摇掉（dist 不含引擎代码）。
 */

/**
 * fetch 的统一入口；返回 Response 或与之形状兼容的对象（ok / status / headers.get / json() / text()）。
 * @param {string} url
 * @param {RequestInit} [init]
 * @returns {Promise<Response|{ok: boolean, status: number, headers: {get: Function}, json: Function, text: Function}>}
 */
export async function apiFetch(url, init) {
  if (__GRIDER_ENGINE__ === 'pyodide') {
    const { engineFetch } = await import('./engine/pyodideClient.js');
    return engineFetch(url, init);
  }
  return fetch(url, init);
}
