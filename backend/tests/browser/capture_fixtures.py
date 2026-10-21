"""抓取在线引擎对比测试所需的真实响应 → backend/tests/fixtures/browser/（需要网络，原生 requests）。

用法（在 backend 目录）：python -m tests.browser.capture_fixtures [--out DIR]

所有 fixture 键均由生产代码的 URL 构造器生成（NIT 21），保证 fixture 钩子与运行时请求的 URL 完全一致：
- 腾讯日线第 1 页 sh510300 (fq="")、sh000001 (日历)
- 腾讯行情 q=sh510300
- 东财 pingzhongdata/110011.js
- 鱼盆大盘清单（GET /api/screener/fish-basin?board=index）所需的一切：
  各 sina_index/hk/us 的腾讯页、两只 COMEX 期货 JSONP、以及 realtime_maps 实际构造的批量行情 URL
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
    futures_url, history_symbol, realtime_quote_url, tencent_page_url,
)
from app.services.providers.browser_market_data import HTTP_TIMEOUT_MS, _USER_AGENT
from app.services.providers.eastmoney import PINGZHONG_URL
from app.services.providers.tencent import TX_KLINE_URL, TX_QUOTE_URL, to_tencent_symbol

DEFAULT_OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "browser")
_PACE_SECONDS = 0.4


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
            continue  # 腾讯无数据的中证指数（932000）：在线版按“不支持”处理，无需 fixture
        plan.append((tencent_page_url(symbol), f"tx_kline_{symbol.replace('.', '_')}_p1.json"))
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


def _fetch_bytes(url: str) -> bytes:
    import requests  # 仅抓取脚本使用

    resp = requests.get(url, timeout=HTTP_TIMEOUT_MS / 1000.0,
                        headers={"User-Agent": _USER_AGENT, "Referer": "https://gu.qq.com/"})
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    return resp.content


def capture(out_dir: str, only: Optional[List[str]] = None) -> Dict:
    os.makedirs(out_dir, exist_ok=True)
    entries: Dict[str, str] = {}
    failures: Dict[str, str] = {}
    for url, filename in planned_requests():
        if only and not any(o in url for o in only):
            continue
        try:
            body = _fetch_bytes(url)
        except Exception as e:  # noqa: BLE001
            failures[url] = f"{type(e).__name__}: {e}"
            print(f"FAIL {filename}: {failures[url]}")
            continue
        with open(os.path.join(out_dir, filename), "wb") as f:
            f.write(body)
        entries[url] = filename
        print(f"ok   {filename}  {len(body)} bytes  sha256={hashlib.sha256(body).hexdigest()[:12]}")
        time.sleep(_PACE_SECONDS)
    index = {"captured_at": date.today().isoformat(), "entries": entries}
    if failures:
        index["failures"] = failures
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    print(f"captured {len(entries)} entries, {len(failures)} failures -> {os.path.join(out_dir, 'index.json')}")
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
