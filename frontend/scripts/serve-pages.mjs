#!/usr/bin/env node
/**
 * 模拟 GitHub Pages 的本地静态服务器（design.md §7.4，供 E2E 与 curl 校验）。
 *
 * 规则：
 * - GET <prefix>            → 301 Location: <prefix>/
 * - GET <prefix>/           → index.html 200
 * - <prefix>/<existing file> → 200 + 按扩展名的 MIME，Cache-Control: no-store
 * - <prefix>/<other>        → 404 状态 + 404.html 正文（SPA 深链接回退，与 Pages 行为一致）
 * - 非 prefix 路径           → 404 纯文本
 *
 * 用法：node scripts/serve-pages.mjs --dir dist --prefix /grider --port 8010
 */
import { createReadStream, existsSync, statSync } from 'node:fs';
import http from 'node:http';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND_DIR = path.resolve(SCRIPT_DIR, '..');
const DEFAULTS = { dir: 'dist', prefix: '/grider', port: '8010', host: '127.0.0.1' };
const MIME = {
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.zip': 'application/zip',
  '.whl': 'application/octet-stream',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.txt': 'text/plain; charset=utf-8',
};
const FALLBACK_MIME = 'application/octet-stream';

/**
 * @param {string[]} argv
 * @returns {{dir: string, prefix: string, port: number, host: string}}
 */
function parseArgs(argv) {
  const opts = { ...DEFAULTS };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i].replace(/^--/, '');
    if (key in opts && argv[i + 1] !== undefined) {
      opts[key] = argv[i + 1];
      i += 1;
    }
  }
  const prefix = `/${opts.prefix.replace(/^\/+|\/+$/g, '')}`;
  return { dir: path.resolve(FRONTEND_DIR, opts.dir), prefix, port: Number(opts.port), host: opts.host };
}

/**
 * 发送文件（status 可为 404，用于 404.html 回退）。
 * @param {http.ServerResponse} res
 * @param {string} filePath
 * @param {number} status
 */
function sendFile(res, filePath, status) {
  const type = MIME[path.extname(filePath).toLowerCase()] || FALLBACK_MIME;
  res.writeHead(status, {
    'Content-Type': type,
    'Content-Length': statSync(filePath).size,
    'Cache-Control': 'no-store',
  });
  createReadStream(filePath).pipe(res);
}

function sendText(res, status, text) {
  res.writeHead(status, { 'Content-Type': 'text/plain; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(text);
}

/**
 * 把 URL 路径安全地映射到 dir 之下的文件；越界或不存在返回 null。
 * @param {string} dir
 * @param {string} rel
 * @returns {string|null}
 */
function resolveFile(dir, rel) {
  const decoded = decodeURIComponent(rel);
  const full = path.resolve(dir, `.${decoded}`);
  if (!full.startsWith(dir + path.sep) && full !== dir) return null;
  if (!existsSync(full) || !statSync(full).isFile()) return null;
  return full;
}

function main() {
  const { dir, prefix, port, host } = parseArgs(process.argv.slice(2));
  if (!existsSync(path.join(dir, 'index.html'))) {
    console.error(`[serve-pages] ${dir}/index.html 不存在（先运行 npm run build:pages）`);
    process.exit(1);
  }
  const notFoundPage = path.join(dir, '404.html');

  const server = http.createServer((req, res) => {
    const url = new URL(req.url, `http://${req.headers.host || 'localhost'}`);
    const pathname = url.pathname;

    if (pathname === prefix) {
      res.writeHead(301, { Location: `${prefix}/${url.search}` });
      res.end();
      return;
    }
    if (!pathname.startsWith(`${prefix}/`)) {
      sendText(res, 404, 'Not Found');
      return;
    }
    const rel = pathname.slice(prefix.length);
    if (rel === '/') {
      sendFile(res, path.join(dir, 'index.html'), 200);
      return;
    }
    const file = resolveFile(dir, rel);
    if (file) {
      sendFile(res, file, 200);
      return;
    }
    if (existsSync(notFoundPage)) {
      sendFile(res, notFoundPage, 404);
      return;
    }
    sendText(res, 404, 'Not Found');
  });

  server.listen(port, host, () => {
    console.error(`[serve-pages] http://${host}:${port}${prefix}/ ← ${dir}`);
  });
}

main();
