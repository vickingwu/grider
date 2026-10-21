"""浏览器(Pyodide)在线版数据层：BrowserMarketData(DataService)。

只覆盖 DataService 中“触网”的方法，对外契约（search_by_ticker / get_latest_price / get_daily_data /
get_5min_kline / get_trading_calendar）全部继承。网络经两个可注入钩子完成：

- http_get_text(url, encoding, ttl)       -> str            （腾讯 kline/行情、东财 push2his）
- fetch_script_vars(url, names, ttl)      -> {name: value}  （无 CORS 的脚本源：pingzhongdata、新浪 JSONP）

本地默认实现用 requests（仅在函数内部导入）；worker 内由 app.browser.bootstrap 注入 JS 桥实现。
缓存 TTL 由调用方按用途选择（实时行情 0、名称 1 天、最新页按 15:05 收口、历史页 7 天、脚本 1 小时）。
本模块不得导入 pyodide；钩子包装统一把任意异常转为 ProviderError。
"""
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from app.algorithms.backtest.models import KBar
from app.services.data_service import (
    DataService, _CACHE, _CACHE_LOCK, _ETF_NAME_MAP, _INDEX_MAP, _cache_get, _cache_set, _classify, _is_index,
)
from app.services.providers.eastmoney import (
    EM_KLINE_URL, PINGZHONG_URL, extract_js_vars, parse_em_kline, parse_networth_trend, secid_for,
)
from app.services.providers.errors import ProviderError
from app.services.providers.sina_jsonp import SINA_KLINE_URL, parse_sina_kline, sina_var_name
from app.services.providers.tencent import (
    TENCENT_LOTS_TO_SHARES, TX_KLINE_URL, TX_MKLINE_URL, TX_PAGE_SIZE, TX_QUOTE_URL, KlineRow,
    parse_kline_json, parse_mkline_json, parse_quote, to_tencent_symbol,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 常量：TTL 策略 / 超时 / 重试 / 时区
# ---------------------------------------------------------------------------
TTL_REALTIME = 0            # qt.gtimg.cn/q= 实时行情（鱼盆）：不读不存
TTL_NAME = 86400            # qt.gtimg.cn/q= 取名称
TTL_LATEST_PAGE = 3600      # 腾讯 end="" 最新页上限（与本地 1 小时历史缓存一致）
TTL_FIXED_PAGE = 7 * 86400  # 腾讯固定 end 的不复权历史页（不可变）
TTL_SCRIPT = 3600           # pingzhongdata / 新浪 JSONP / 东财 push2his
HTTP_TIMEOUT_MS = 12000     # 与 data_service._DEFAULT_HTTP_TIMEOUT 一致
TX_RETRIES = 2              # 腾讯单页总尝试次数
TX_RETRY_DELAY = 0.5        # 腾讯重试间隔(秒)
TX_MIN_INTERVAL = 0.15      # 腾讯翻页节流(秒)
EM_MIN_INTERVAL = 2.0       # 东财兜底节流(秒)
CN_TZ = timezone(timedelta(hours=8))
SESSION_SETTLED = dtime(15, 5)  # A 股日线在新浪侧收口的时刻（北京时间）
_MAX_PAGES = 60                 # 翻页保险丝（60 页 ≈ 150 年）

HttpGetText = Callable[[str, Optional[str], int], str]
FetchScriptVars = Callable[[str, List[str], int], Dict[str, Any]]

_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_OHLC = ["open", "high", "low", "close"]
_FQ_BY_TYPE = {"ETF": "", "STOCK": "qfq", "INDEX": ""}
_EM_FQT_BY_TYPE = {"ETF": 1, "STOCK": 1, "INDEX": 0}


def _now_cn() -> datetime:
    """当前北京时间（测试可 monkeypatch）。"""
    return datetime.now(CN_TZ)


# ---------------------------------------------------------------------------
# 取数钩子：默认 requests 实现 + setter + 统一异常包装
# ---------------------------------------------------------------------------
_http_get_text_hook: Optional[HttpGetText] = None
_fetch_script_vars_hook: Optional[FetchScriptVars] = None


def _requests_get_text(url: str, encoding: Optional[str], ttl: int) -> str:
    """本地默认 HTTP 实现；requests 仅在此函数内部导入。ttl 在本地忽略（Python 层缓存另行处理）。"""
    import requests  # noqa: WPS433  本地专用，保持 providers 包无模块级 requests 依赖

    resp = requests.get(url, timeout=HTTP_TIMEOUT_MS / 1000.0,
                        headers={"User-Agent": _USER_AGENT, "Referer": "https://gu.qq.com/"})
    if resp.status_code != 200:
        raise ProviderError(f"HTTP {resp.status_code}")
    resp.encoding = encoding or resp.apparent_encoding
    return resp.text


def _requests_fetch_script_vars(url: str, names: List[str], ttl: int) -> Dict[str, Any]:
    """本地默认脚本变量实现：下载脚本文本后用 extract_js_vars 提取。"""
    return extract_js_vars(_requests_get_text(url, script_encoding_for(url), ttl), names)


def decode_response_bytes(data: bytes, encoding: Optional[str]) -> str:
    """响应字节 → 文本：优先调用方指定编码；否则带 BOM 用 utf-8-sig，默认 utf-8（strict）。"""
    if encoding:
        return data.decode(encoding)
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    return data.decode("utf-8")


def script_encoding_for(url: str) -> str:
    """脚本源的文本编码：fund.eastmoney.com 为 UTF-8(带 BOM)，新浪 JSONP 为 GBK。"""
    return "utf-8-sig" if "fund.eastmoney.com" in url else "gbk"


def set_http_get_text(fn: Optional[HttpGetText]) -> None:
    """注入 http_get_text 实现；None 恢复 requests 默认。"""
    global _http_get_text_hook
    _http_get_text_hook = fn


def set_fetch_script_vars(fn: Optional[FetchScriptVars]) -> None:
    """注入 fetch_script_vars 实现；None 恢复 requests 默认。"""
    global _fetch_script_vars_hook
    _fetch_script_vars_hook = fn


def http_get_text(url: str, encoding: Optional[str], ttl: int) -> str:
    """统一入口：任何异常（含 JS 侧异常）都转为 ProviderError。"""
    fn = _http_get_text_hook or _requests_get_text
    try:
        return fn(url, encoding, ttl)
    except ProviderError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ProviderError(f"{type(e).__name__}: {e}") from e


def fetch_script_vars(url: str, names: List[str], ttl: int) -> Dict[str, Any]:
    """统一入口：任何异常都转为 ProviderError。"""
    fn = _fetch_script_vars_hook or _requests_fetch_script_vars
    try:
        return fn(url, list(names), ttl)
    except ProviderError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ProviderError(f"{type(e).__name__}: {e}") from e


# ---------------------------------------------------------------------------
# TTL 策略
# ---------------------------------------------------------------------------
def latest_page_ttl(now: Optional[datetime] = None) -> int:
    """腾讯最新页(end="")与 browser:hist 条目的 TTL。

    交易日 15:05 前只缓存到 15:05（下限 60 秒），避免盘中未收盘 K 线在收盘后被当作日线继续服务；
    其余时间 3600 秒。
    """
    now = now or _now_cn()
    if now.weekday() >= 5 or now.time() >= SESSION_SETTLED:
        return TTL_LATEST_PAGE
    settle = now.replace(hour=SESSION_SETTLED.hour, minute=SESSION_SETTLED.minute, second=0, microsecond=0)
    return max(60, min(TTL_LATEST_PAGE, int((settle - now).total_seconds())))


def _page_ttl(end: str, fq: str, now: Optional[datetime] = None) -> int:
    """腾讯日线单页 TTL。

    end == ""      → latest_page_ttl(now)（最新页，盘中按 15:05 收口）
    fq == ""       → TTL_FIXED_PAGE（不复权/指数历史不可变，7 天）
    qfq/hfq 固定页 → TTL_LATEST_PAGE：复权序列在每次分红送转后整体重算，旧页与新页基准可能不同；
                      Python 侧 browser:hist 的向前扩页依赖该条目 < 1 小时，故 qfq 页 TTL 永不高于 TTL_LATEST_PAGE。
    """
    if end == "":
        return latest_page_ttl(now)
    if fq == "":
        return TTL_FIXED_PAGE
    return TTL_LATEST_PAGE


# ---------------------------------------------------------------------------
# 节流 / 重试
# ---------------------------------------------------------------------------
_LAST_CALL_AT: Dict[str, float] = {}


def _pace(source: str, min_interval: float) -> None:
    """同一数据源两次调用之间至少间隔 min_interval 秒（兜底路径可接受 sleep）。"""
    if min_interval <= 0:
        return
    last = _LAST_CALL_AT.get(source)
    if last is not None:
        wait = min_interval - (time.time() - last)
        if wait > 0:
            time.sleep(wait)
    _LAST_CALL_AT[source] = time.time()


def _tencent_get_text(url: str, encoding: Optional[str], ttl: int) -> str:
    """腾讯请求：最多 TX_RETRIES 次尝试，间隔 TX_RETRY_DELAY。"""
    last_err: Optional[ProviderError] = None
    for attempt in range(1, TX_RETRIES + 1):
        try:
            return http_get_text(url, encoding, ttl)
        except ProviderError as e:
            last_err = e
            if attempt < TX_RETRIES and TX_RETRY_DELAY > 0:
                time.sleep(TX_RETRY_DELAY)
    raise last_err or ProviderError("腾讯请求失败")


# ---------------------------------------------------------------------------
# 翻页与盘中规则
# ---------------------------------------------------------------------------
def _dedupe_rows(rows: List[KlineRow]) -> List[KlineRow]:
    """按日期去重（保留最后一次出现），日期严格升序。"""
    by_date: Dict[str, KlineRow] = {}
    for row in rows:
        by_date[row.date] = row
    return [by_date[d] for d in sorted(by_date)]


def load_history_pages(http_get_text_fn: HttpGetText, symbol: str, fq: str, since: Optional[str],
                       end: str = "", now: Optional[datetime] = None) -> Tuple[List[KlineRow], bool]:
    """从 end（"" = 最新页）向前翻腾讯日线页，直到覆盖 since 或到达上市日。

    返回 (按日期去重升序的行, complete)。complete=True 表示已到上市日（某页不足 640 行或为空）。
    每页 TTL 由 _page_ttl(end, fq, now) 决定。
    """
    rows: List[KlineRow] = []
    complete = False
    for _ in range(_MAX_PAGES):
        url = TX_KLINE_URL.format(symbol=symbol, start="", end=end, fq=fq)
        page = parse_kline_json(http_get_text_fn(url, None, _page_ttl(end, fq, now)), symbol, fq)
        if not page:
            complete = True
            break
        rows = page + rows
        if len(page) < TX_PAGE_SIZE:
            complete = True
            break
        earliest = page[0].date
        if since and earliest <= since:
            break
        next_end = (date.fromisoformat(earliest) - timedelta(days=1)).isoformat()
        if end and next_end >= end:  # 无进展，防止死循环
            break
        end = next_end
        _pace("tencent", TX_MIN_INTERVAL)
    return _dedupe_rows(rows), complete


def drop_live_bar(df: pd.DataFrame, now: Optional[datetime] = None) -> pd.DataFrame:
    """盘中(交易日北京时间 < 15:05)剔除 date == 今日 的未收盘 K 线，使口径与新浪日线(不含当日)一致。"""
    now = now or _now_cn()
    if df is None or len(df) == 0 or "date" not in df.columns:
        return df
    if now.weekday() >= 5 or now.time() >= SESSION_SETTLED:
        return df
    today = now.strftime("%Y-%m-%d")
    return df[df["date"] != today].reset_index(drop=True)


def _rows_to_frame(rows: List[KlineRow]) -> pd.DataFrame:
    """腾讯行 → 统一日线列；成交量 手→股(×100)，成交额按均价估算。"""
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["volume"] = df["volume"] * TENCENT_LOTS_TO_SHARES
    df["amount"] = ((df["open"] + df["close"] + df["high"] + df["low"]) / 4) * df["volume"]
    return df


# ---------------------------------------------------------------------------
# BrowserMarketData
# ---------------------------------------------------------------------------
class BrowserMarketData(DataService):
    """浏览器在线版数据服务：腾讯为主，东财/新浪 JSONP 兜底，场外基金走 pingzhongdata。"""

    def __init__(self) -> None:
        super().__init__()
        self.provider = "browser"

    # ------------------------------------------------------------------
    # 名称
    # ------------------------------------------------------------------
    def _lookup_name(self, code: str, is_etf: bool) -> str:
        """内置映射优先（与本地显示名一致），否则腾讯行情名称；查不到回落为代码。缓存 name:{code} 1 天。

        指数名称由 search_by_ticker 先经 _INDEX_MAP 解析，不会进入本方法。
        """
        cache_key = f"name:{code}"
        cached = _cache_get(cache_key)
        if cached:
            return cached

        name = code
        if code in _ETF_NAME_MAP:
            name = _ETF_NAME_MAP[code]
        else:
            _, exchange = _classify(code)
            symbol = to_tencent_symbol(code, "ETF" if is_etf else "STOCK", exchange)
            try:
                text = http_get_text(TX_QUOTE_URL.format(symbols=symbol), "gbk", TTL_NAME)
                quote = parse_quote(text).get(symbol)
                if quote is not None and quote.name:
                    name = quote.name
            except ProviderError as e:
                logger.warning(f"名称查询失败，使用代码兜底: {code}, {e}")

        if name and name != code:
            _cache_set(cache_key, name, ttl=TTL_NAME)
        return name

    def _lookup_fund_name(self, code: str) -> str:
        """场外基金名称：与净值共用同一次 pingzhongdata 加载（fS_name）。缓存 fundname:{code} 1 天。"""
        cache_key = f"fundname:{code}"
        cached = _cache_get(cache_key)
        if cached:
            return cached
        self._get_full_history(code, "FUND")
        return _cache_get(cache_key) or f"场外基金{code}"

    # ------------------------------------------------------------------
    # 历史日线
    # ------------------------------------------------------------------
    def _fetch_daily(self, code: str, sec_type: str, start_compact: str, end_compact: str) -> Optional[pd.DataFrame]:
        """区间切片；把紧凑起始日转为 ISO since 传给 _get_full_history，让翻页知道要回溯多远。"""
        since = f"{start_compact[:4]}-{start_compact[4:6]}-{start_compact[6:]}" if start_compact else None
        full = self._get_full_history(code, sec_type, since)
        if full is None or len(full) == 0:
            return None

        start_d = since
        end_d = f"{end_compact[:4]}-{end_compact[4:6]}-{end_compact[6:]}" if end_compact else None

        df = full
        if start_d:
            df = df[df["date"] >= start_d]
        if end_d:
            df = df[df["date"] <= end_d]
        return df.reset_index(drop=True)

    def _get_full_history(self, code: str, sec_type: str, since: Optional[str] = None) -> Optional[pd.DataFrame]:
        """全量/足量历史。

        FUND → browser:fund:{code}（1 小时）。
        其余 → browser:hist:{type}:{code} = {df(剔除盘中 K 线后), earliest, complete, all_dates(剔除前), source}，
        TTL = latest_page_ttl()；since 未覆盖且来源为腾讯时向前扩页并前置。
        """
        if sec_type == "FUND":
            return self._get_full_fund_history(code)

        cache_key = f"browser:hist:{sec_type}:{code}"
        entry = _cache_get(cache_key)
        if entry is not None:
            if entry["complete"] or (since is not None and entry["earliest"] <= since):
                return entry["df"]
            extended = self._extend_backwards(code, sec_type, entry, since)
            if extended is not None:
                _cache_set(cache_key, extended, ttl=latest_page_ttl())
                return extended["df"]
            return entry["df"]

        loaded = self._load_from_chain(code, sec_type, since)
        if loaded is None:
            return None
        raw_df, complete, source = loaded
        raw_df = raw_df.dropna(subset=_OHLC).sort_values("date").reset_index(drop=True)
        if len(raw_df) == 0:
            return None
        df = drop_live_bar(raw_df)
        entry = {
            "df": df,
            "earliest": str(raw_df["date"].iloc[0]),
            "complete": complete,
            "all_dates": raw_df["date"].tolist(),
            "source": source,
        }
        _cache_set(cache_key, entry, ttl=latest_page_ttl())
        return df

    def _get_full_fund_history(self, code: str) -> Optional[pd.DataFrame]:
        cache_key = f"browser:fund:{code}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        df = self._fetch_full_open_fund(code)
        if df is None or len(df) == 0:
            return None
        df = df.dropna(subset=_OHLC).reset_index(drop=True)
        _cache_set(cache_key, df, ttl=TTL_SCRIPT)
        return df

    def _extend_backwards(self, code: str, sec_type: str, entry: Dict[str, Any],
                          since: Optional[str]) -> Optional[Dict[str, Any]]:
        """缓存条目未覆盖 since：从 earliest-1 天继续向前翻腾讯页并前置。非腾讯来源视为已完整，不扩页。"""
        if entry.get("source") != "tencent":
            return None
        symbol = self._tencent_symbol(code, sec_type)
        fq = _FQ_BY_TYPE.get(sec_type, "")
        end = (date.fromisoformat(entry["earliest"]) - timedelta(days=1)).isoformat()
        try:
            rows, complete = load_history_pages(_tencent_get_text, symbol, fq, since, end=end)
        except ProviderError as e:
            logger.warning(f"腾讯向前扩页失败 {code}: {e}")
            return None
        if not rows:
            return {**entry, "complete": True}
        older = _rows_to_frame(rows)
        older = older[older["date"] < entry["earliest"]].dropna(subset=_OHLC)
        df = pd.concat([older, entry["df"]], ignore_index=True)
        df = df.drop_duplicates(subset=["date"], keep="last").sort_values("date").reset_index(drop=True)
        all_dates = sorted(set(older["date"].tolist()) | set(entry["all_dates"]))
        return {
            "df": df,
            "earliest": all_dates[0] if all_dates else entry["earliest"],
            "complete": complete,
            "all_dates": all_dates,
            "source": "tencent",
        }

    @staticmethod
    def _tencent_symbol(code: str, sec_type: str) -> str:
        """与本地 _fetch_full_sina 一致：交易所由 _classify 推导（_get_full_history 不接收 exchange_code）。"""
        _, exchange = _classify(code)
        return to_tencent_symbol(code, sec_type, exchange)

    def _load_from_chain(self, code: str, sec_type: str,
                         since: Optional[str]) -> Optional[Tuple[pd.DataFrame, bool, str]]:
        """数据源链：腾讯 → 东财(节流，bj 跳过) → 新浪 JSONP(仅 ETF/INDEX)。每跳失败记 WARNING；全部失败返回 None。"""
        symbol = self._tencent_symbol(code, sec_type)
        fq = _FQ_BY_TYPE.get(sec_type, "")

        # 1) 腾讯 fqkline（主源）
        try:
            rows, complete = load_history_pages(_tencent_get_text, symbol, fq, since)
            if rows:
                return _rows_to_frame(rows), complete, "tencent"
            logger.warning(f"[tencent] 日线为空: {symbol}")
        except ProviderError as e:
            logger.warning(f"[tencent] 日线获取失败 {symbol}: {e}")

        # 2) 东财 push2his（兜底，节流 ≥ 2 秒）
        secid = secid_for(symbol)
        if secid is None:
            logger.warning(f"[eastmoney] 跳过(未知 secid 前缀): {symbol}")
        else:
            try:
                _pace("eastmoney", EM_MIN_INTERVAL)
                url = EM_KLINE_URL.format(secid=secid, fqt=_EM_FQT_BY_TYPE.get(sec_type, 1))
                df = parse_em_kline(http_get_text(url, None, TTL_SCRIPT))
                if len(df):
                    return df, True, "eastmoney"
                logger.warning(f"[eastmoney] 日线为空: {symbol}")
            except ProviderError as e:
                logger.warning(f"[eastmoney] 日线获取失败 {symbol}: {e}")

        # 3) 新浪 JSONP（第三跳，仅不复权口径的 ETF/INDEX；STOCK 需要前复权，禁用）
        if sec_type == "STOCK":
            logger.warning(f"[sina] 个股需前复权，第三跳禁用: {symbol}")
            return None
        try:
            var = sina_var_name(symbol)
            values = fetch_script_vars(SINA_KLINE_URL.format(var=var, symbol=symbol), [var], TTL_SCRIPT)
            rows = values.get(var)
            if rows is None:
                raise ProviderError("新浪 JSONP 返回 null")
            df = parse_sina_kline(rows)
            if df is not None and len(df):
                return df, True, "sina"
            logger.warning(f"[sina] 日线为空: {symbol}")
        except ProviderError as e:
            logger.warning(f"[sina] 日线获取失败 {symbol}: {e}")
        return None

    def _fetch_full_open_fund(self, code: str) -> Optional[pd.DataFrame]:
        """场外基金：一次 pingzhongdata 加载同时得到 fS_name（缓存 fundname:{code} 1 天）与 Data_netWorthTrend。"""
        try:
            values = fetch_script_vars(PINGZHONG_URL.format(code=code),
                                       ["fS_name", "Data_netWorthTrend"], TTL_SCRIPT)
        except ProviderError as e:
            logger.error(f"场外基金净值获取失败: {code}, {e}")
            return None
        name = values.get("fS_name")
        if name:
            _cache_set(f"fundname:{code}", str(name), ttl=TTL_NAME)
        return parse_networth_trend(values.get("Data_netWorthTrend"))

    # ------------------------------------------------------------------
    # K 线（回测）
    # ------------------------------------------------------------------
    def get_5min_kline(self, ticker: str, exchange_code: str,
                       start_date: str, end_date: str, type: str = "STOCK") -> List[KBar]:
        """在线版回测统一使用日线（与本地分钟数据不可达时的兜底一致）。"""
        code = str(ticker).strip()
        return self._daily_as_kbars(code, type, start_date, end_date)

    def get_intraday_kline(self, ticker: str, exchange_code: str, period: str = "m60", count: int = 640) -> List[KBar]:
        """腾讯分钟线（仅用于数据校验，不接任何路由）。"""
        code = str(ticker).strip()
        if _is_index(code):
            sec_type = "INDEX"
        else:
            sec_type = "ETF" if _classify(code)[0] else "STOCK"
        symbol = to_tencent_symbol(code, sec_type, exchange_code or _classify(code)[1])
        text = http_get_text(TX_MKLINE_URL.format(symbol=symbol, period=period, count=count), None, TTL_REALTIME)
        kbars: List[KBar] = []
        for row in parse_mkline_json(text, symbol, period):
            try:
                kbars.append(KBar(
                    time=datetime.strptime(row.time, "%Y%m%d%H%M"),
                    open=float(row.open), high=float(row.high), low=float(row.low), close=float(row.close),
                    volume=int(row.volume * TENCENT_LOTS_TO_SHARES) if pd.notna(row.volume) else 0,
                ))
            except (ValueError, TypeError):
                continue
        kbars.sort(key=lambda k: k.time)
        return kbars

    # ------------------------------------------------------------------
    # 交易日历
    # ------------------------------------------------------------------
    def get_trading_calendar(self, exchange_code: str, limit: int = 5,
                             start_date: str = None, end_date: str = None) -> List[str]:
        """交易日历（降序）：取自 sh000001 腾讯日线剔除盘中 K 线前的日期列表(all_dates)。

        当日出现与否取决于腾讯是否已有当日 K 线——与本地 tool_trade_date_hist_sina（日历含今日而日线不含）一致。
        """
        try:
            today = _now_cn().strftime("%Y-%m-%d")
            since = start_date or (_now_cn() - timedelta(days=limit * 2 + 10)).strftime("%Y-%m-%d")
            df = self._get_full_history("000001", "INDEX", since)
            entry = _cache_get("browser:hist:INDEX:000001")
            if entry is not None:
                all_dates = list(entry["all_dates"])
            else:
                all_dates = df["date"].tolist() if df is not None else []
            past = [d for d in all_dates if d <= today]
            if not past:
                raise ValueError("无法获取交易日历")

            if start_date and end_date:
                rng = [d for d in past if start_date <= d <= end_date]
                rng.sort(reverse=True)
                return rng
            past.sort(reverse=True)
            return past[:limit]
        except Exception as e:
            logger.error(f"获取交易日历失败: {e}")
            raise

    # ------------------------------------------------------------------
    # 兼容方法
    # ------------------------------------------------------------------
    def get_cache_stats(self) -> dict:
        with _CACHE_LOCK:
            return {"entries": len(_CACHE), "provider": "browser"}
