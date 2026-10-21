"""鱼盆模型在线取数路径（fish_basin_service 在 akshare 缺失时调用）。

- sina_index / hk / us / csindex(腾讯有数据者) → 一页腾讯 fqkline（fq=""，640 根 ≫ 模型所需的 ~60 根）
- comex → 新浪外盘期货 JSONP（fetch_script_vars）
- 其它来源或腾讯无数据的中证指数 → UnsupportedOnlineError（文案以 UNSUPPORTED_PREFIX 开头，直接展示）
- 网络/解析失败 → WARNING 并返回 None，由原 _evaluate_one 产生「数据不足以计算20日线」
"""
from typing import Dict, List, Optional, Tuple

import pandas as pd

from app.services.data_service import _cache_set
from app.services.fish_basin_service import FISH_BASIN_INDICES, _qq_symbol
from app.services.providers.browser_market_data import (
    TTL_REALTIME, TTL_SCRIPT, _tencent_get_text, drop_live_bar, fetch_script_vars, http_get_text, latest_page_ttl,
)
from app.services.providers.errors import UNSUPPORTED_PREFIX, ProviderError, UnsupportedOnlineError
from app.services.providers.sina_jsonp import (
    SINA_FUTURES_URL, SINA_KLINE_URL, futures_var_name, parse_sina_futures, parse_sina_kline, sina_var_name,
)
from app.services.providers.tencent import TX_KLINE_URL, TX_QUOTE_URL, parse_kline_json, parse_quote
from app.utils.logger import get_logger

logger = get_logger(__name__)

# 美股 ETF 的腾讯符号（SPY 在腾讯的市场后缀为 .AM：usSPY.N 仅返回 1 根，2026-10-21 实测）
US_TENCENT_SYMBOLS: Dict[str, str] = {"QQQ": "usQQQ.OQ", "SPY": "usSPY.AM"}
# A 股指数腾讯页少于此根数时回落新浪 JSONP（bj899050 腾讯仅返回当日 1 根，2026-10-21 实测）
MIN_HISTORY_ROWS = 60
CSINDEX_UNSUPPORTED_MSG = f"{UNSUPPORTED_PREFIX}(中证官网数据浏览器无法直接访问)，请使用本地版"
GENERIC_UNSUPPORTED_MSG = f"{UNSUPPORTED_PREFIX}，请使用本地版"

RealtimeHit = Tuple[float, Optional[float]]


def tencent_page_url(symbol: str) -> str:
    """鱼盆用的单页腾讯日线 URL（fq=""、最新页）。"""
    return TX_KLINE_URL.format(symbol=symbol, start="", end="", fq="")


def futures_url(sym: str) -> str:
    """COMEX 外盘期货的新浪 JSONP URL。"""
    return SINA_FUTURES_URL.format(var=futures_var_name(sym), sym=sym)


def sina_kline_url(symbol: str) -> str:
    """A 股指数腾讯页不足时的新浪日线 JSONP URL（与 BrowserMarketData 第三跳同一构造）。"""
    return SINA_KLINE_URL.format(var=sina_var_name(symbol), symbol=symbol)


def history_symbol(item: Dict) -> Optional[str]:
    """日线取数用的腾讯符号；无腾讯源（comex / 腾讯无数据的中证 / 未映射的美股）返回 None。"""
    source = item.get("source")
    sym = str(item.get("sym", "")).strip()
    if source == "sina_index":
        return sym
    if source == "hk":
        return f"hk{sym}"
    if source == "us":
        return US_TENCENT_SYMBOLS.get(sym)
    if source == "csindex":
        return _qq_symbol(item)
    return None


def realtime_symbols(indices: List[Dict]) -> List[str]:
    """实时行情批量请求的腾讯符号列表（去重、保持顺序）。"""
    symbols: List[str] = []
    for item in indices:
        symbol = _realtime_symbol(item)
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return symbols


def realtime_quote_url(indices: List[Dict]) -> Optional[str]:
    """realtime_maps 实际请求的批量行情 URL（无符号时 None）。"""
    symbols = realtime_symbols(indices)
    if not symbols:
        return None
    return TX_QUOTE_URL.format(symbols=",".join(symbols))


def _tencent_page(symbol: str) -> Optional[pd.DataFrame]:
    """一页腾讯日线 → DataFrame[date, close, volume]（volume 仅用于量比，不换算单位）。"""
    rows = parse_kline_json(_tencent_get_text(tencent_page_url(symbol), None, latest_page_ttl()), symbol, "")
    if not rows:
        return None
    return pd.DataFrame({
        "date": [r.date for r in rows],
        "close": [r.close for r in rows],
        "volume": [r.volume for r in rows],
    })


def _sina_page(symbol: str) -> Optional[pd.DataFrame]:
    """新浪日线 JSONP → DataFrame[date, close, volume]（不复权，与腾讯 fq="" 口径一致）。"""
    var = sina_var_name(symbol)
    values = fetch_script_vars(sina_kline_url(symbol), [var], TTL_SCRIPT)
    df = parse_sina_kline(values.get(var))
    if df is None or len(df) == 0:
        return None
    return df[["date", "close", "volume"]]


def _a_share_index_page(symbol: str) -> Optional[pd.DataFrame]:
    """A 股指数：腾讯页优先；根数不足 MIN_HISTORY_ROWS 时回落新浪 JSONP，新浪也失败则保留腾讯结果。"""
    df = _tencent_page(symbol)
    if df is not None and len(df) >= MIN_HISTORY_ROWS:
        return df
    logger.warning(f"[fish-basin] 腾讯日线仅 {0 if df is None else len(df)} 根，回落新浪 JSONP: {symbol}")
    try:
        fallback = _sina_page(symbol)
    except ProviderError as e:
        logger.warning(f"[fish-basin] 新浪 JSONP 回落失败 {symbol}: {e}")
        return df
    return fallback if fallback is not None else df


def _sina_futures(sym: str) -> Optional[pd.DataFrame]:
    """新浪外盘期货日线 → DataFrame[date, close, volume]。"""
    var = futures_var_name(sym)
    values = fetch_script_vars(futures_url(sym), [var], TTL_SCRIPT)
    df = parse_sina_futures(values.get(var))
    if df is None or len(df) == 0:
        return None
    return df[["date", "close", "volume"]]


def _load_by_source(item: Dict) -> Tuple[Optional[pd.DataFrame], bool]:
    """按 source 取数，返回 (df, 是否 A 股口径)。无在线源 → UnsupportedOnlineError。"""
    source = item.get("source")
    sym = str(item.get("sym", "")).strip()
    if source == "sina_index":
        return _a_share_index_page(sym), True
    if source == "hk":
        return _tencent_page(f"hk{sym}"), False
    if source == "us":
        mapped = US_TENCENT_SYMBOLS.get(sym)
        if mapped is None:
            raise UnsupportedOnlineError(GENERIC_UNSUPPORTED_MSG)
        return _tencent_page(mapped), False
    if source == "comex":
        return _sina_futures(sym), False
    if source == "csindex":
        qq_symbol = _qq_symbol(item)
        if qq_symbol is None:
            raise UnsupportedOnlineError(CSINDEX_UNSUPPORTED_MSG)
        df = _tencent_page(qq_symbol)
        if df is None or len(df) == 0:
            raise UnsupportedOnlineError(CSINDEX_UNSUPPORTED_MSG)
        return df, True
    raise UnsupportedOnlineError(GENERIC_UNSUPPORTED_MSG)


def fetch_daily(item: Dict, cache_key: str, ttl: int) -> Optional[pd.DataFrame]:
    """鱼盆日线（在线版）：返回 DataFrame[date(str), close, volume] 升序，并以 df.to_dict("list") 写入 cache_key。

    UnsupportedOnlineError 向上抛出（由 _evaluate_one 记为 error 文案）；其它失败 → WARNING + None。
    """
    try:
        df, is_a_share = _load_by_source(item)
    except UnsupportedOnlineError:
        raise
    except Exception as e:  # noqa: BLE001  ProviderError / 网络 / 解析
        logger.warning(f"鱼盆在线取数失败 {item.get('code')}({item.get('source')}:{item.get('sym')}): {e}")
        return None
    if df is None or len(df) == 0:
        return None

    df = df.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)
    df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    if is_a_share:
        df = drop_live_bar(df)
    df = df[["date", "close", "volume"]].reset_index(drop=True)
    _cache_set(cache_key, df.to_dict("list"), ttl)
    return df


def _realtime_symbol(item: Dict) -> Optional[str]:
    """实时行情用的腾讯符号；无实时源（us/comex/腾讯无数据的中证）返回 None。"""
    source = item.get("source")
    sym = str(item.get("sym", "")).strip()
    if source == "sina_index":
        return sym
    if source == "hk":
        return f"hk{sym}"
    if source == "csindex":
        return _qq_symbol(item)
    return None


def realtime_maps(indices: Optional[List[Dict]] = None) -> Dict:
    """一次腾讯批量行情 → {"idx": {sina_sym: (price, pct)}, "hk": {HSI: ...}, "qq": {tencent_sym: ...}, "comex": {}}。

    pct 为小数（腾讯 parts[32] 百分数 / 100）。ttl=TTL_REALTIME(0)：不读不存缓存。
    COMEX 无可用实时源，保持空表（评估时自动回退收盘价）。失败 → WARNING + 空表。
    """
    indices = indices or FISH_BASIN_INDICES
    maps: Dict = {"idx": {}, "hk": {}, "qq": {}, "comex": {}}
    url = realtime_quote_url(indices)
    if url is None:
        return maps

    try:
        text = http_get_text(url, "gbk", TTL_REALTIME)
    except ProviderError as e:
        logger.warning(f"鱼盆实时行情拉取失败: {e}")
        return maps
    quotes = parse_quote(text)
    for item in indices:
        symbol = _realtime_symbol(item)
        quote = quotes.get(symbol) if symbol else None
        if quote is None or quote.price is None or quote.price <= 0:
            continue
        hit: RealtimeHit = (float(quote.price), quote.pct)
        source = item.get("source")
        if source == "sina_index":
            maps["idx"][symbol] = hit
        elif source == "hk":
            maps["hk"][str(item.get("sym"))] = hit
        elif source == "csindex":
            maps["qq"][symbol] = hit
    return maps
