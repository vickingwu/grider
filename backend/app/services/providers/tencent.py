"""腾讯行情：URL 构造、代码映射与纯解析函数（无网络）。

- fqkline/get 日线：行布局 [date, open, close, high, low, volume(手), (dividend dict)]
- kline/mkline 分钟线：行布局 [time "YYYYMMDDHHMM", open, close, high, low, volume]
- qt.gtimg.cn/q= 行情：GBK 文本，parts[1] 名称、[3] 现价、[30] 时间戳、[32] 涨跌幅(%)
"""
import json
import math
from typing import Dict, List, NamedTuple, Optional

from app.services.data_service import _INDEX_MAP
from app.services.providers.errors import ProviderError

# 日线分页：param={symbol},day,{start},{end},640,{fq}；end="" 表示最新一页
TX_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,{start},{end},640,{fq}"
# 分钟线：param={symbol},{period},,{count}（仅用于校验，不接路由）
TX_MKLINE_URL = "https://ifzq.gtimg.cn/appstock/app/kline/mkline?param={symbol},{period},,{count}"
# 批量行情（GBK）
TX_QUOTE_URL = "https://qt.gtimg.cn/q={symbols}"
# 腾讯成交量单位为“手”，×100 换算为“股”（与新浪日线口径一致）
TENCENT_LOTS_TO_SHARES = 100
# 一页最多 640 根
TX_PAGE_SIZE = 640


class KlineRow(NamedTuple):
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class MKlineRow(NamedTuple):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: float


class Quote(NamedTuple):
    name: str
    price: Optional[float]
    timestamp: str
    pct: Optional[float]


def to_tencent_symbol(code: str, sec_type: str, exchange_code: str) -> str:
    """证券代码 → 腾讯符号：INDEX 取 _INDEX_MAP（sh000300/sz399006/bj899050），其余按交易所加 sh/sz 前缀。"""
    code = str(code).strip()
    if sec_type == "INDEX" and code in _INDEX_MAP:
        return _INDEX_MAP[code][0]
    prefix = "sh" if exchange_code == "XSHG" else "sz"
    return f"{prefix}{code}"


def _to_float(value) -> float:
    """非数字 → NaN（上层按 OHLC dropna 丢弃）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _fq_key(fq: str) -> str:
    if fq == "qfq":
        return "qfqday"
    if fq == "hfq":
        return "hfqday"
    return "day"


def parse_kline_json(text: str, symbol: str, fq: str) -> List[KlineRow]:
    """解析 fqkline/get 正文。code != 0 → ProviderError；行索引 2 为 close、3 为 high；第 7 个分红元素忽略。"""
    try:
        payload = json.loads(text)
    except ValueError as e:
        raise ProviderError(f"腾讯日线正文不是 JSON: {e}") from e
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise ProviderError(f"腾讯日线接口返回错误: {payload.get('msg') if isinstance(payload, dict) else payload}")
    data = payload.get("data") or {}
    series = data.get(symbol) or {}
    rows = series.get(_fq_key(fq))
    if rows is None:
        # 部分标的无复权序列时腾讯回落到 day
        rows = series.get("day") or []
    out: List[KlineRow] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        out.append(KlineRow(
            date=str(row[0]),
            open=_to_float(row[1]),
            close=_to_float(row[2]),
            high=_to_float(row[3]),
            low=_to_float(row[4]),
            volume=_to_float(row[5]),
        ))
    return out


def parse_mkline_json(text: str, symbol: str, period: str) -> List[MKlineRow]:
    """解析 kline/mkline 正文；行布局 [time, open, close, high, low, volume]。"""
    try:
        payload = json.loads(text)
    except ValueError as e:
        raise ProviderError(f"腾讯分钟线正文不是 JSON: {e}") from e
    if not isinstance(payload, dict) or payload.get("code") != 0:
        raise ProviderError(f"腾讯分钟线接口返回错误: {payload.get('msg') if isinstance(payload, dict) else payload}")
    series = (payload.get("data") or {}).get(symbol) or {}
    rows = series.get(period) or []
    out: List[MKlineRow] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        out.append(MKlineRow(
            time=str(row[0]),
            open=_to_float(row[1]),
            close=_to_float(row[2]),
            high=_to_float(row[3]),
            low=_to_float(row[4]),
            volume=_to_float(row[5]),
        ))
    return out


def parse_quote(text: str) -> Dict[str, Quote]:
    """解析 qt.gtimg.cn/q= 正文 → {symbol: Quote}。

    每行形如 v_sh510300="1~沪深300ETF华泰柏瑞~510300~4.229~...";
    要求 ≥ 33 个字段；未知代码返回 v_pv_none_match="1"; 或空字符串 → 不产生条目（调用方用 .get）。
    涨跌幅 parts[32] 为百分数，转换为小数 (/100)。
    """
    result: Dict[str, Quote] = {}
    if not text:
        return result
    for line in text.split(";"):
        line = line.strip()
        if '="' not in line:
            continue
        key, _, raw = line.partition("=")
        key = key.strip()
        if not key.startswith("v_"):
            continue
        symbol = key[2:]
        if symbol == "pv_none_match":
            continue
        value = raw.strip().strip('"')
        if not value:
            continue
        parts = value.split("~")
        if len(parts) < 33:
            continue
        price = _to_float(parts[3]) if parts[3] else None
        if price is not None and math.isnan(price):
            price = None
        pct = None
        if parts[32]:
            pct_raw = _to_float(parts[32])
            pct = None if math.isnan(pct_raw) else pct_raw / 100.0
        result[symbol] = Quote(name=parts[1], price=price, timestamp=parts[30], pct=pct)
    return result
