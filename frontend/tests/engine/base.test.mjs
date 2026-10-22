/**
 * 基路径工具 node:test（design.md §6.4，NIT 27）：joinBase 与 getBaseUrl 的回退值。
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { getBaseUrl, joinBase } from '../../src/shared/utils/base.js';

test("joinBase('/', 'analysis/x') === '/analysis/x'", () => {
  assert.equal(joinBase('/', 'analysis/x'), '/analysis/x');
});

test("joinBase('/grider/', '/analysis/x') === '/grider/analysis/x'", () => {
  assert.equal(joinBase('/grider/', '/analysis/x'), '/grider/analysis/x');
});

test('joinBase tolerates a base without trailing slash and keeps the query string', () => {
  assert.equal(joinBase('/grider', 'ma-backtest?code=510300'), '/grider/ma-backtest?code=510300');
});

test("getBaseUrl() falls back to '/' outside Vite", () => {
  assert.equal(getBaseUrl(), '/');
});
