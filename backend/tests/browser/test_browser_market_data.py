"""BrowserMarketData 单元测试：翻页、单位换算、缓存扩展、盘中规则、日历、名称、数据源链、TTL 策略。"""
import json
import os
from datetime import date, datetime, timedelta

import pandas as pd
import pytest

from app.services.data_service import _cache_get
from app.services.providers import browser_market_data as bmd
from app.services.providers.browser_market_data import (
    CN_TZ, TTL_FIXED_PAGE, TTL_LATEST_PAGE, TTL_NAME, TTL_SCRIPT, BrowserMarketData, ProviderError,
    drop_live_bar, latest_page_ttl, load_history_pages,
)
from app.services.providers.eastmoney import EM_KLINE_URL, PINGZHONG_URL
from app.services.providers.sina_jsonp import SINA_KLINE_URL, sina_var_name
from app.services.providers.tencent import TX_KLINE_URL, TX_QUOTE_URL, TENCENT_LOTS_TO_SHARES
from tests.browser.synth import page_body, quote_body, synth_rows, tencent_page_json, tencent_pages

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "browser")
WED = date(2026, 10, 21)            # 周三
WED_AFTER_CLOSE = datetime(2026, 10, 21, 16, 0, tzinfo=CN_TZ)
WED_IN_SESSION = datetime(2026, 10, 21, 10, 0, tzinfo=CN_TZ)


def _url(symbol: str, fq: str = "", end: str = "") -> str:
    return TX_KLINE_URL.format(symbol=symbol, start="", end=end, fq=fq)


@pytest.fixture
def after_close(monkeypatch):
    """固定“现在”为周三 16:00，使合成页(页尾 2026-10-21)不触发盘中剔除。"""
    monkeypatch.setattr(bmd, "_now_cn", lambda: WED_AFTER_CLOSE)
    return WED_AFTER_CLOSE


# ---------------------------------------------------------------- 翻页
def test_load_history_pages_three_pages_to_listing(recording_hook, after_close):
    pages, all_rows = tencent_pages("sh510300", fq="", total_rows=1500, end=WED)
    hook = recording_hook(pages).install()
    rows, complete = load_history_pages(hook.http_get_text, "sh510300", "", since=None)
    assert complete is True
    assert len(rows) == 1500 and len(pages) == 3
    assert [r.date for r in rows] == [r[0] for r in all_rows]
    assert hook.urls() == list(pages.keys())  # 第 1 页 end=""，之后 end=最早日期-1 天


def test_load_history_pages_stops_when_since_covered(recording_hook, after_close):
    pages, all_rows = tencent_pages("sh510300", fq="", total_rows=1500, end=WED)
    hook = recording_hook(pages).install()
    since = all_rows[500][0]  # 位于第 2 页范围内（第 1 页覆盖 860..1499）
    rows, complete = load_history_pages(hook.http_get_text, "sh510300", "", since=since)
    assert complete is False
    assert len(rows) == 1280 and len(hook.calls) == 2
    assert rows[0].date <= since


def test_load_history_pages_short_page_means_listing_start(recording_hook, after_close):
    hook = recording_hook({_url("sh510300"): tencent_page_json("sh510300", rows=400, end=WED)}).install()
    rows, complete = load_history_pages(hook.http_get_text, "sh510300", "", since="2000-01-01")
    assert complete is True and len(rows) == 400 and len(hook.calls) == 1


def test_load_history_pages_dedupes_overlap_keeping_latest(recording_hook, after_close):
    pages, all_rows = tencent_pages("sh510300", fq="", total_rows=700, end=WED)
    urls = list(pages.keys())
    # 让第 2 页末尾重复第 1 页首行但收盘不同：去重应保留第 1 页（后出现）的值
    page2 = json.loads(pages[urls[1]])
    dup = list(all_rows[60])
    dup[2] = "9.999"
    page2["data"]["sh510300"]["day"].append(dup)
    pages[urls[1]] = json.dumps(page2)
    hook = recording_hook(pages).install()
    rows, _ = load_history_pages(hook.http_get_text, "sh510300", "", since=None)
    assert len(rows) == 700
    dates = [r.date for r in rows]
    assert dates == sorted(dates) and len(set(dates)) == 700
    assert next(r for r in rows if r.date == dup[0]).close == float(all_rows[60][2])


def test_empty_first_page_is_complete_and_empty(recording_hook, after_close):
    hook = recording_hook({_url("sh999999"): page_body("sh999999", "", [])}).install()
    rows, complete = load_history_pages(hook.http_get_text, "sh999999", "", None)
    assert rows == [] and complete is True


# ---------------------------------------------------------------- 单位 / 成交额 / 类型
@pytest.mark.parametrize("code,sec_type,symbol,fq", [
    ("510300", "ETF", "sh510300", ""),
    ("600519", "STOCK", "sh600519", "qfq"),
    ("000300", "INDEX", "sh000300", ""),
    ("159915", "ETF", "sz159915", ""),
])
def test_full_history_lots_to_shares_and_amount(recording_hook, after_close, code, sec_type, symbol, fq):
    body = tencent_page_json(symbol, fq=fq, rows=50, end=WED)
    hook = recording_hook({_url(symbol, fq): body}).install()
    df = BrowserMarketData()._get_full_history(code, sec_type)
    raw = json.loads(body)["data"][symbol][{"qfq": "qfqday"}.get(fq, "day")]
    assert len(df) == 50
    assert df["volume"].iloc[0] == pytest.approx(float(raw[0][5]) * TENCENT_LOTS_TO_SHARES)
    o, c, h, l = (float(raw[0][i]) for i in (1, 2, 3, 4))
    assert df["amount"].iloc[0] == pytest.approx((o + c + h + l) / 4 * df["volume"].iloc[0])
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume", "amount"]


def test_fetch_daily_converts_compact_since_and_slices(recording_hook, after_close):
    pages, all_rows = tencent_pages("sh510300", total_rows=1500, end=WED)
    hook = recording_hook(pages).install()
    svc = BrowserMarketData()
    start = all_rows[1300][0].replace("-", "")
    end = all_rows[1400][0].replace("-", "")
    df = svc._fetch_daily("510300", "ETF", start, end)
    assert len(hook.calls) == 1  # since 在第 1 页范围内，不翻第 2 页
    assert df["date"].iloc[0] == all_rows[1300][0] and df["date"].iloc[-1] == all_rows[1400][0]
    assert len(df) == 101


def test_cache_extension_backwards(recording_hook, after_close):
    pages, all_rows = tencent_pages("sh510300", total_rows=1500, end=WED)
    hook = recording_hook(pages).install()
    svc = BrowserMarketData()
    recent = all_rows[1200][0]
    df1 = svc._get_full_history("510300", "ETF", since=recent)
    entry = _cache_get("browser:hist:ETF:510300")
    assert len(df1) == 640 and entry["complete"] is False and entry["earliest"] == all_rows[860][0]
    assert len(hook.calls) == 1

    # 命中缓存且已覆盖 since → 不再请求
    assert len(svc._get_full_history("510300", "ETF", since=all_rows[900][0])) == 640
    assert len(hook.calls) == 1

    # since 更早 → 从 earliest-1 天继续向前翻一页并前置
    df2 = svc._get_full_history("510300", "ETF", since=all_rows[500][0])
    assert len(hook.calls) == 2
    assert hook.urls()[1] == list(pages.keys())[1]
    entry = _cache_get("browser:hist:ETF:510300")
    assert len(df2) == 1280 and entry["earliest"] == all_rows[220][0] and entry["complete"] is False
    assert df2["date"].is_monotonic_increasing and df2["date"].is_unique

    # 一直翻到上市日
    df3 = svc._get_full_history("510300", "ETF", since=None)
    assert len(df3) == 1500 and _cache_get("browser:hist:ETF:510300")["complete"] is True


# ---------------------------------------------------------------- 盘中规则
def _frame_with_today(today: str) -> pd.DataFrame:
    return pd.DataFrame({"date": ["2026-10-19", "2026-10-20", today], "open": [1, 1, 1], "high": [1, 1, 1],
                         "low": [1, 1, 1], "close": [1, 1, 1], "volume": [1, 1, 1]})


def test_drop_live_bar_in_session_drops_today():
    df = _frame_with_today("2026-10-21")
    out = drop_live_bar(df, now=datetime(2026, 10, 21, 9, 30, tzinfo=CN_TZ))
    assert out["date"].tolist() == ["2026-10-19", "2026-10-20"]


def test_drop_live_bar_after_settle_keeps_today():
    df = _frame_with_today("2026-10-21")
    out = drop_live_bar(df, now=datetime(2026, 10, 21, 15, 10, tzinfo=CN_TZ))
    assert len(out) == 3


def test_drop_live_bar_saturday_noop():
    df = _frame_with_today("2026-10-24")  # 周六
    out = drop_live_bar(df, now=datetime(2026, 10, 24, 10, 0, tzinfo=CN_TZ))
    assert len(out) == 3


def test_full_history_applies_live_bar_rule_but_keeps_all_dates(recording_hook, monkeypatch):
    monkeypatch.setattr(bmd, "_now_cn", lambda: WED_IN_SESSION)
    hook = recording_hook({_url("sh000001"): tencent_page_json("sh000001", rows=30, end=WED)}).install()
    df = BrowserMarketData()._get_full_history("000001", "INDEX")
    entry = _cache_get("browser:hist:INDEX:000001")
    assert df["date"].iloc[-1] == "2026-10-20"
    assert entry["all_dates"][-1] == "2026-10-21"
    assert entry["earliest"] == entry["all_dates"][0]


# ---------------------------------------------------------------- 交易日历
def test_trading_calendar_from_all_dates_includes_live_date(recording_hook, monkeypatch):
    monkeypatch.setattr(bmd, "_now_cn", lambda: WED_IN_SESSION)
    hook = recording_hook({_url("sh000001"): tencent_page_json("sh000001", rows=30, end=WED)}).install()
    svc = BrowserMarketData()
    cal = svc.get_trading_calendar("XSHG", limit=5)
    assert cal[0] == "2026-10-21"                      # 日历含今日（腾讯已有当日 K 线）
    assert cal == sorted(cal, reverse=True) and len(cal) == 5
    assert svc.get_daily_data("000001", "XSHG", "INDEX", "2026-10-01", "2026-10-21")["date"].iloc[-1] == "2026-10-20"
    rng = svc.get_trading_calendar("XSHG", start_date="2026-10-14", end_date="2026-10-20")
    assert rng == ["2026-10-20", "2026-10-19", "2026-10-16", "2026-10-15", "2026-10-14"]
    assert hook.urls().count(_url("sh000001")) == 1  # 缓存复用


def test_trading_calendar_empty_raises(recording_hook, after_close):
    recording_hook({_url("sh000001"): page_body("sh000001", "", [])}).install()
    with pytest.raises(ValueError, match="无法获取交易日历"):
        BrowserMarketData().get_trading_calendar("XSHG")


# ---------------------------------------------------------------- 名称
def test_lookup_name_precedence_map_first_then_quote(recording_hook):
    hook = recording_hook({
        TX_QUOTE_URL.format(symbols="sh600519"): quote_body("sh600519", "贵州茅台", price=1500.0),
        TX_QUOTE_URL.format(symbols="sz000001"): 'v_pv_none_match="1";',
        TX_QUOTE_URL.format(symbols="sz300750"): 'v_sz300750="";',
    }).install()
    svc = BrowserMarketData()
    assert svc._lookup_name("510300", True) == "沪深300ETF"       # _ETF_NAME_MAP 优先，不触网
    assert svc._lookup_name("000300", False) == "沪深300指数"      # 指数名也在 _ETF_NAME_MAP 中
    assert hook.calls == []
    assert svc._lookup_name("600519", False) == "贵州茅台"
    assert svc._lookup_name("600519", False) == "贵州茅台" and len(hook.calls) == 1  # name:{code} 缓存
    assert svc._lookup_name("000001", False) == "000001"             # v_pv_none_match → 代码
    assert svc._lookup_name("300750", False) == "300750"             # 空 v_sym → 代码
    assert svc._lookup_name("688981", False) == "688981"             # fixture 缺失(ProviderError) → 代码


def test_search_by_ticker_types(recording_hook, after_close):
    recording_hook({TX_QUOTE_URL.format(symbols="sh600519"): quote_body("sh600519", "贵州茅台")}).install()
    svc = BrowserMarketData()
    assert svc.search_by_ticker("510300") == {"code": "510300", "name": "沪深300ETF", "exchange_code": "XSHG",
                                              "type": "ETF", "management": ""}
    assert svc.search_by_ticker("600519")["type"] == "STOCK"
    assert svc.search_by_ticker("000300")["type"] == "INDEX"


# ---------------------------------------------------------------- 场外基金
def _fund_vars(name="易方达优质精选混合(QDII)"):
    return {"fS_name": name, "Data_netWorthTrend": [
        {"x": 1697040000000, "y": 1.2345}, {"x": 1697126400000, "y": 1.2400}, {"x": 1697385600000, "y": 1.2500},
    ]}


def test_fund_history_and_name_share_one_script_load(recording_hook):
    hook = recording_hook(script_vars={PINGZHONG_URL.format(code="110011"): _fund_vars()}).install()
    svc = BrowserMarketData()
    info = svc.search_by_ticker("110011", sec_type_hint="FUND")
    assert info == {"code": "110011", "name": "易方达优质精选混合(QDII)", "exchange_code": "", "type": "FUND",
                    "management": ""}
    df = svc.get_daily_data("110011", "", "FUND", "2023-10-01", "2023-10-31")
    assert df["date"].tolist() == ["2023-10-12", "2023-10-13", "2023-10-16"]
    assert (df["open"] == df["close"]).all()
    assert len(hook.script_calls) == 1
    assert hook.script_calls[0] == (PINGZHONG_URL.format(code="110011"), ["fS_name", "Data_netWorthTrend"], TTL_SCRIPT)


def test_fund_missing_falls_back_like_native(recording_hook):
    recording_hook().install()  # 任何 URL 都 fixture missing → ProviderError
    svc = BrowserMarketData()
    assert svc._lookup_fund_name("999999") == "场外基金999999"
    assert svc.get_daily_data("999999", "", "FUND") is None


# ---------------------------------------------------------------- 数据源链
def _em_body() -> str:
    with open(os.path.join(FIXTURES, "em_kline_synthetic.json"), "r", encoding="utf-8") as f:
        return f.read()


def test_chain_tencent_fails_then_eastmoney(recording_hook, after_close):
    hook = recording_hook({EM_KLINE_URL.format(secid="1.510300", fqt=1): _em_body()}).install()
    hook.fail_urls.add(_url("sh510300"))
    df = BrowserMarketData()._get_full_history("510300", "ETF")
    assert len(df) == 4 and "change_pct" in df.columns
    assert df["volume"].iloc[0] == 89709  # em 路径保持“手”
    assert hook.urls().count(_url("sh510300")) == bmd.TX_RETRIES
    entry = _cache_get("browser:hist:ETF:510300")
    assert entry["source"] == "eastmoney" and entry["complete"] is True


def test_chain_index_uses_fqt0(recording_hook, after_close):
    hook = recording_hook({EM_KLINE_URL.format(secid="1.000300", fqt=0): _em_body()}).install()
    hook.fail_urls.add(_url("sh000300"))
    assert BrowserMarketData()._get_full_history("000300", "INDEX") is not None


def test_chain_eastmoney_fails_then_sina_for_etf(recording_hook, after_close):
    var = sina_var_name("sh510300")
    sina_url = SINA_KLINE_URL.format(var=var, symbol="sh510300")
    hook = recording_hook(script_vars={sina_url: {var: [
        {"day": "2026-10-13", "open": "4.313", "high": "4.384", "low": "4.286", "close": "4.345", "volume": "1081547864"},
        {"day": "2026-10-14", "open": "4.35", "high": "4.40", "low": "4.30", "close": "4.38", "volume": "1000"},
    ]}}).install()
    hook.fail_urls.update({_url("sh510300"), EM_KLINE_URL.format(secid="1.510300", fqt=1)})
    df = BrowserMarketData()._get_full_history("510300", "ETF")
    assert df["date"].tolist() == ["2026-10-13", "2026-10-14"]
    assert df["volume"].iloc[0] == 1081547864  # 新浪为“股”，不乘系数
    assert hook.script_calls[0] == (sina_url, [var], TTL_SCRIPT)
    assert _cache_get("browser:hist:ETF:510300")["source"] == "sina"


def test_chain_stock_tertiary_disabled(recording_hook, after_close):
    hook = recording_hook().install()
    hook.fail_urls.update({_url("sh600519", "qfq"), EM_KLINE_URL.format(secid="1.600519", fqt=1)})
    assert BrowserMarketData()._get_full_history("600519", "STOCK") is None
    assert hook.script_calls == []
    assert BrowserMarketData().get_daily_data("600519", "XSHG", "STOCK") is None


def test_chain_bj_skips_eastmoney(recording_hook, after_close):
    hook = recording_hook().install()
    hook.fail_urls.add(_url("bj899050"))
    assert BrowserMarketData()._get_full_history("899050", "INDEX") is None
    assert not any("push2his" in u for u in hook.urls())
    assert len(hook.script_calls) == 1  # 直接到新浪第三跳


def test_sina_null_is_provider_error_then_none(recording_hook, after_close):
    var = sina_var_name("sh510300")
    hook = recording_hook(script_vars={SINA_KLINE_URL.format(var=var, symbol="sh510300"): {var: None}}).install()
    hook.fail_urls.update({_url("sh510300"), EM_KLINE_URL.format(secid="1.510300", fqt=1)})
    assert BrowserMarketData()._get_full_history("510300", "ETF") is None


def test_hook_wrapper_converts_any_exception_to_provider_error():
    def _boom(url, encoding, ttl):
        raise KeyError("js side exploded")

    bmd.set_http_get_text(_boom)
    with pytest.raises(ProviderError):
        bmd.http_get_text("https://x", None, 0)
    bmd.set_fetch_script_vars(lambda u, n, t: (_ for _ in ()).throw(RuntimeError("x")))
    with pytest.raises(ProviderError):
        bmd.fetch_script_vars("https://x", ["a"], 0)


# ---------------------------------------------------------------- K 线 / 统计
def test_get_5min_kline_is_daily(recording_hook, after_close):
    recording_hook({_url("sh510300"): tencent_page_json("sh510300", rows=60, end=WED)}).install()
    svc = BrowserMarketData()
    kbars = svc.get_5min_kline("510300", "XSHG", "2026-09-01", "2026-10-21", "ETF")
    assert len(kbars) > 20
    assert all(k.time.hour == 15 and k.time.minute == 0 for k in kbars)
    assert kbars == sorted(kbars, key=lambda k: k.time)


def test_get_latest_price_shape(recording_hook, after_close):
    body = tencent_page_json("sh510300", rows=60, end=WED)
    recording_hook({_url("sh510300"): body}).install()
    price = BrowserMarketData().get_latest_price("510300", "XSHG", "ETF")
    assert price["date"] == "2026-10-21" and price["change_pct"] is None
    assert set(price) == {"close", "open", "high", "low", "volume", "amount", "date", "change_pct", "data_age_days"}


def test_provider_and_cache_stats():
    svc = BrowserMarketData()
    assert svc.provider == "browser"
    assert svc.get_cache_stats()["provider"] == "browser"
    assert isinstance(svc.get_cache_stats()["entries"], int)


# ---------------------------------------------------------------- TTL 策略
def test_latest_page_ttl_injected_now():
    assert latest_page_ttl(datetime(2026, 10, 21, 14, 30, tzinfo=CN_TZ)) == 2100   # 周三 14:30
    assert latest_page_ttl(datetime(2026, 10, 21, 16, 0, tzinfo=CN_TZ)) == 3600    # 周三 16:00
    assert latest_page_ttl(datetime(2026, 10, 24, 10, 0, tzinfo=CN_TZ)) == 3600    # 周六 10:00
    assert latest_page_ttl(datetime(2026, 10, 21, 15, 4, 30, tzinfo=CN_TZ)) == 60  # 下限 60 秒
    assert latest_page_ttl(datetime(2026, 10, 21, 9, 0, tzinfo=CN_TZ)) == 3600     # 上限 3600


def test_page_ttl_policy():
    now = datetime(2026, 10, 21, 14, 30, tzinfo=CN_TZ)
    assert bmd._page_ttl("", "", now) == latest_page_ttl(now) == 2100
    assert bmd._page_ttl("", "qfq", now) == 2100
    assert bmd._page_ttl("2025-01-01", "qfq", now) == TTL_LATEST_PAGE == 3600   # MEDIUM 1
    assert bmd._page_ttl("2025-01-01", "hfq", now) == 3600
    assert bmd._page_ttl("2025-01-01", "", now) == TTL_FIXED_PAGE == 604800


def test_recording_hook_ttl_policy(recording_hook, monkeypatch):
    now = datetime(2026, 10, 21, 14, 30, tzinfo=CN_TZ)
    monkeypatch.setattr(bmd, "_now_cn", lambda: now)
    pages_etf, _ = tencent_pages("sh510300", fq="", total_rows=700, end=WED)
    pages_stock, _ = tencent_pages("sh600519", fq="qfq", total_rows=700, end=WED, start_price=1500)
    bodies = {**pages_etf, **pages_stock, TX_QUOTE_URL.format(symbols="sh600519"): quote_body("sh600519", "贵州茅台")}
    hook = recording_hook(bodies).install()
    svc = BrowserMarketData()

    svc._lookup_name("600519", False)
    assert hook.calls[-1][2] == TTL_NAME == 86400

    svc._get_full_history("510300", "ETF")
    etf_calls = [c for c in hook.calls if "sh510300" in c[0]]
    assert etf_calls[0][2] == latest_page_ttl(now) == 2100           # 最新页
    assert etf_calls[1][2] == 604800                                  # 固定 end，fq=""

    svc._get_full_history("600519", "STOCK")
    stock_calls = [c for c in hook.calls if "sh600519" in c[0] and "fqkline" in c[0]]
    assert stock_calls[0][2] == 2100
    assert stock_calls[1][2] == 3600                                  # 固定 end，fq="qfq"


def test_browser_hist_cache_ttl_equals_latest_page_ttl(recording_hook, monkeypatch):
    now = datetime(2026, 10, 21, 14, 30, tzinfo=CN_TZ)
    monkeypatch.setattr(bmd, "_now_cn", lambda: now)
    recorded = []
    real_set = bmd._cache_set

    def _recording_set(key, value, ttl=None):
        recorded.append((key, ttl))
        return real_set(key, value, ttl)

    monkeypatch.setattr(bmd, "_cache_set", _recording_set)
    recording_hook({_url("sh510300"): tencent_page_json("sh510300", rows=50, end=WED)}).install()
    BrowserMarketData()._get_full_history("510300", "ETF")
    hist = [t for k, t in recorded if k == "browser:hist:ETF:510300"]
    assert hist == [latest_page_ttl(now)] == [2100]


def test_realtime_ttl_constant_is_zero():
    assert bmd.TTL_REALTIME == 0 and bmd.HTTP_TIMEOUT_MS == 12000
    assert bmd.SESSION_SETTLED.hour == 15 and bmd.SESSION_SETTLED.minute == 5
