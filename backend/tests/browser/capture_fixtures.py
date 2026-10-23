"""抓取在线引擎对比测试所需的真实响应 → backend/tests/fixtures/browser/（需要网络，原生 requests）。

用法（在 backend 目录）：python -m tests.browser.capture_fixtures [--out DIR]

所有 fixture 键均由生产代码的 URL 构造器生成（NIT 21），保证 fixture 钩子与运行时请求的 URL 完全一致：
- 腾讯日线第 1 页 sh510300 (fq="")、sh000001 (日历)
- 腾讯行情 q=sh510300
- 东财 pingzhongdata/110011.js
- 鱼盆大盘清单（GET /api/screener/fish-basin?board=index）所需的一切：
  各 sina_index/hk/us 的腾讯页、腾讯无数据的中证指数（932000）的东财 push2his 页、两只 COMEX 期货 JSONP、
  以及 realtime_maps 实际构造的批量行情 URL
正文以原始字节保存（GBK 行情 / UTF-8 BOM 脚本原样），由 testing.install_fixture_hooks 按调用方编码解码。
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date
from typing import Dict, List, Optional, Tuple

from app.services.fish_basin_service import FISH_BASIN_INDICES
from app.services.providers.browser_fish_basin import (  # history_symbol 内部即 fish_basin_service._qq_symbol
    EM_KLINE_HOSTS, MIN_HISTORY_ROWS, em_index_url, em_secid, futures_url, history_symbol, realtime_quote_url,
    sina_kline_url, tencent_page_url,
)
from app.services.providers.browser_market_data import HTTP_TIMEOUT_MS, _USER_AGENT
from app.services.providers.eastmoney import PINGZHONG_URL
from app.services.providers.tencent import TX_KLINE_URL, TX_QUOTE_URL, parse_kline_json, to_tencent_symbol

DEFAULT_OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "browser")
_PACE_SECONDS = 0.4
# 腾讯页 URL → (符号, 新浪 JSONP URL, 文件名)：仅当腾讯正文根数 < MIN_HISTORY_ROWS 时抓取（与运行时回落条件一致）
_SINA_FALLBACKS: Dict[str, Tuple[str, str, str]] = {}


def _needs_sina_fallback(url: str, body: bytes) -> Optional[Tuple[str, str]]:
    """腾讯 A 股指数页根数不足时返回 (新浪 URL, 文件名)，否则 None。"""
    fallback = _SINA_FALLBACKS.get(url)
    if fallback is None:
        return None
    symbol, sina_url, filename = fallback
    try:
        rows = parse_kline_json(body.decode("utf-8"), symbol, "")
    except Exception:  # noqa: BLE001  解析失败按“不足”处理，运行时同样会回落
        rows = []
    if len(rows) >= MIN_HISTORY_ROWS:
        return None
    return sina_url, filename


def planned_requests() -> List[Tuple[str, str]]:
    """(URL, 文件名) 列表；文件名可读且稳定。"""
    plan: List[Tuple[str, str]] = [
        (TX_KLINE_URL.format(symbol=to_tencent_symbol("510300", "ETF", "XSHG"), start="", end="", fq=""),
         "tx_kline_sh510300_p1.json"),
        (TX_QUOTE_URL.format(symbols="sh510300"), "tx_quote_sh510300.txt"),
        (TX_KLINE_URL.format(symbol=to_tencent_symbol("000001", "INDEX", "XSHG"), start="", end="", fq=""),
         "tx_kline_sh000001_p1.json"),
        (PINGZHONG_URL.format(code="110011"), "em_pingzhong_110011.js"),
    ]
    for item in FISH_BASIN_INDICES:
        source = item["source"]
        if source == "comex":
            plan.append((futures_url(item["sym"]), f"sina_futures_{item['sym']}.js"))
            continue
        symbol = history_symbol(item)
        if symbol is None:
            if source == "csindex":  # 腾讯无数据的中证指数（932000）：运行时走东财 push2his（第 1 主机）
                plan.append((em_index_url(EM_KLINE_HOSTS[0], em_secid(item)), f"em_kline_{item['code']}.json"))
            continue
        plan.append((tencent_page_url(symbol), f"tx_kline_{symbol.replace('.', '_')}_p1.json"))
        if source == "sina_index":
            # 腾讯页不足 MIN_HISTORY_ROWS 根时运行时会回落新浪 JSONP；capture() 据实际正文决定是否抓取
            _SINA_FALLBACKS[tencent_page_url(symbol)] = (symbol, sina_kline_url(symbol), f"sina_kline_{symbol}.js")
    quote_url = realtime_quote_url(FISH_BASIN_INDICES)
    if quote_url:
        plan.append((quote_url, "tx_quote_fishbasin_index.txt"))
    # 去重（保持顺序）
    seen = set()
    unique: List[Tuple[str, str]] = []
    for url, name in plan:
        if url in seen:
            continue
        seen.add(url)
        unique.append((url, name))
    return unique


def _fetch_bytes_once(url: str) -> bytes:
    import requests  # 仅抓取脚本使用

    resp = requests.get(url, timeout=HTTP_TIMEOUT_MS / 1000.0,
                        headers={"User-Agent": _USER_AGENT, "Referer": "https://gu.qq.com/"})
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    return resp.content


def _fetch_bytes(url: str) -> bytes:
    """抓取正文；东财 push2his 第 1 主机失败（部分出口 IP 连接被重置）时换第 2 主机重试一次，正文仍以原 URL 入 index。"""
    try:
        return _fetch_bytes_once(url)
    except Exception:  # noqa: BLE001
        host0, host1 = EM_KLINE_HOSTS[0], EM_KLINE_HOSTS[1]
        if f"https://{host0}/" not in url:
            raise
        print(f"     东财 {host0} 失败，改用 {host1} 重试一次")
        time.sleep(_PACE_SECONDS)
        return _fetch_bytes_once(url.replace(f"https://{host0}/", f"https://{host1}/", 1))


def capture(out_dir: str, only: Optional[List[str]] = None) -> Dict:
    """全量抓取重写 index.json；--only 部分重抓时合并进现有 index（保留其余条目与 captured_at，成功则从 failures 移除）。"""
    os.makedirs(out_dir, exist_ok=True)
    entries: Dict[str, str] = {}
    failures: Dict[str, str] = {}
    captured_at = date.today().isoformat()
    index_path = os.path.join(out_dir, "index.json")
    if only and os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            existing = json.load(f)
        entries = dict(existing.get("entries") or {})
        failures = dict(existing.get("failures") or {})
        captured_at = existing.get("captured_at") or captured_at
    queue: List[Tuple[str, str]] = [(u, n) for u, n in planned_requests() if not only or any(o in u for o in only)]
    while queue:
        url, filename = queue.pop(0)
        try:
            body = _fetch_bytes(url)
        except Exception as e:  # noqa: BLE001
            failures[url] = f"{type(e).__name__}: {e}"
            print(f"FAIL {filename}: {failures[url]}")
            continue
        with open(os.path.join(out_dir, filename), "wb") as f:
            f.write(body)
        entries[url] = filename
        failures.pop(url, None)
        print(f"ok   {filename}  {len(body)} bytes  sha256={hashlib.sha256(body).hexdigest()[:12]}")
        fallback = _needs_sina_fallback(url, body)
        if fallback is not None:
            print(f"     腾讯页不足 {MIN_HISTORY_ROWS} 根，追加新浪 JSONP: {fallback[1]}")
            queue.insert(0, fallback)
        time.sleep(_PACE_SECONDS)
    index = {"captured_at": captured_at, "entries": entries}
    if failures:
        index["failures"] = failures
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(f"captured {len(entries)} entries, {len(failures)} failures -> {index_path}")
    return index


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="抓取在线引擎 fixture")
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--only", nargs="*", help="仅抓取 URL 含这些子串的条目")
    parser.add_argument("--plan", action="store_true", help="只打印计划，不联网")
    args = parser.parse_args(argv)
    if args.plan:
        for url, name in planned_requests():
            print(f"{name}\t{url}")
        return 0
    index = capture(args.out, args.only)
    return 1 if index.get("failures") else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
