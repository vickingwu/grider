"""确定性的腾讯格式合成数据（页尾落在今天，永不过期）。

- business_days_ending(n, end): 最近 n 个工作日（周一至周五，不含节假日判断）
- tencent_page_json(symbol, fq, rows, dividend_at=None): 单页 fqkline/get 正文
- tencent_pages(symbol, fq, total_rows, ...): 多页 {url: body}，URL 与 load_history_pages 的翻页规则一致
- quote_body(symbol, name, price, pct): qt.gtimg.cn/q= 正文（≥ 33 字段）
- em_kline_json(code, rows): 东财 push2his kline/get 正文（鱼盆中证指数 fallback）
"""
import json
import random
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from app.services.providers.tencent import TX_KLINE_URL, TX_PAGE_SIZE


def business_days_ending(n: int, end: Optional[date] = None) -> List[date]:
    """以 end（默认今天）为最后一天往前取 n 个工作日，升序。"""
    end = end or date.today()
    days: List[date] = []
    cursor = end
    while len(days) < n:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    return days


def synth_rows(symbol: str, days: List[date], start_price: float = 4.2, seed: int = 7,
               dividend_at: Optional[int] = None) -> List[list]:
    """按日期序列生成腾讯行 [date, open, close, high, low, volume(手), (dividend)]，收盘为确定性随机游走。"""
    rng = random.Random(f"{symbol}:{seed}")
    rows: List[list] = []
    close = start_price
    for i, d in enumerate(days):
        open_ = close * (1 + rng.uniform(-0.004, 0.004))
        close = max(0.5, close * (1 + rng.gauss(0, 0.011)))
        high = max(open_, close) * (1 + rng.uniform(0, 0.006))
        low = min(open_, close) * (1 - rng.uniform(0, 0.006))
        volume = rng.uniform(4_000_000, 12_000_000)
        row = [d.isoformat(), f"{open_:.3f}", f"{close:.3f}", f"{high:.3f}", f"{low:.3f}", f"{volume:.3f}"]
        if dividend_at is not None and i == dividend_at:
            row.append({"fhsp": "每10股派0.5元", "lx": 2})
        rows.append(row)
    return rows


def _fq_key(fq: str) -> str:
    return {"qfq": "qfqday", "hfq": "hfqday"}.get(fq, "day")


def page_body(symbol: str, fq: str, rows: List[list]) -> str:
    """包装成 fqkline/get 正文。"""
    return json.dumps({"code": 0, "msg": "", "data": {symbol: {_fq_key(fq): rows, "qt": {}, "mx_price": {}}}})


def tencent_page_json(symbol: str, fq: str = "", rows: int = 400, end: Optional[date] = None,
                      start_price: float = 4.2, seed: int = 7, dividend_at: Optional[int] = None) -> str:
    """单页正文（rows < 640 时 load_history_pages 视为已到上市日）。"""
    return page_body(symbol, fq, synth_rows(symbol, business_days_ending(rows, end), start_price, seed, dividend_at))


def tencent_pages(symbol: str, fq: str = "", total_rows: int = 1500, end: Optional[date] = None,
                  start_price: float = 4.2, seed: int = 7) -> Tuple[Dict[str, str], List[list]]:
    """多页：返回 ({url: body}, 全部行升序)。第 1 页 end=""，后续 end=(上一页最早日期 − 1 天)。"""
    all_rows = synth_rows(symbol, business_days_ending(total_rows, end), start_price, seed)
    pages: Dict[str, str] = {}
    end_param = ""
    remaining = list(all_rows)
    while remaining:
        page = remaining[-TX_PAGE_SIZE:]
        remaining = remaining[:-TX_PAGE_SIZE]
        pages[TX_KLINE_URL.format(symbol=symbol, start="", end=end_param, fq=fq)] = page_body(symbol, fq, page)
        earliest = date.fromisoformat(page[0][0])
        end_param = (earliest - timedelta(days=1)).isoformat()
    return pages, all_rows


def em_kline_json(code: str, rows: int = 400, end: Optional[date] = None, start_price: float = 1000.0,
                  seed: int = 7) -> str:
    """东财 push2his kline/get 正文：klines 每行 11 个字段（date,open,close,high,low,volume,amount,...）。"""
    lines: List[str] = []
    for d, open_, close, high, low, volume in synth_rows(code, business_days_ending(rows, end), start_price, seed):
        amount = float(volume) * float(close) * 100
        lines.append(f"{d},{open_},{close},{high},{low},{volume},{amount:.2f},1.00,0.10,1.0,0.5")
    return json.dumps({"rc": 0, "rt": 17, "svr": 1, "lt": 1, "full": 0, "dlmkts": "",
                       "data": {"code": code, "market": 2, "name": f"idx{code}", "klines": lines}})


def quote_body(symbol: str, name: str, price: float = 4.229, pct: float = 0.02,
               timestamp: str = "20261021150000") -> str:
    """qt.gtimg.cn/q= 单行正文：parts[1]=name, [3]=price, [30]=timestamp, [32]=pct(%)。"""
    parts = [""] * 50
    parts[0] = "1"
    parts[1] = name
    parts[2] = symbol[2:]
    parts[3] = f"{price:.3f}"
    parts[30] = timestamp
    parts[32] = f"{pct:.2f}"
    return f'v_{symbol}="{"~".join(parts)}";\n'
