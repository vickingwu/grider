"""鱼盆模型在线路径单元测试。"""
from datetime import date, datetime

import pytest

from app.services import fish_basin_service as fbs
from app.services.data_service import _cache_get
from app.services.providers import browser_fish_basin as bfb
from app.services.providers import browser_market_data as bmd
from app.services.providers.browser_market_data import CN_TZ
from app.services.providers.errors import UNSUPPORTED_PREFIX, UnsupportedOnlineError
from app.services.providers.sina_jsonp import SINA_FUTURES_URL, futures_var_name, sina_var_name
from app.services.providers.tencent import TX_QUOTE_URL
from app.utils import concurrency
from tests.browser.synth import page_body, quote_body, tencent_page_json

WED = date(2026, 10, 21)
WED_AFTER_CLOSE = datetime(2026, 10, 21, 16, 0, tzinfo=CN_TZ)
WED_IN_SESSION = datetime(2026, 10, 21, 10, 0, tzinfo=CN_TZ)


def _item(code, source, sym, name="x"):
    return {"code": code, "name": name, "source": source, "sym": sym, "cat": "t"}


@pytest.fixture
def after_close(monkeypatch):
    monkeypatch.setattr(bmd, "_now_cn", lambda: WED_AFTER_CLOSE)


def test_realtime_maps_ttl_zero_and_pct_fraction(recording_hook):
    indices = [_item("000688", "sina_index", "sh000688"), _item("HSI", "hk", "HSI"),
               _item("000813", "csindex", "000813"), _item("931865", "csindex", "931865"),
               _item("GC", "comex", "GC"), _item("QQQ", "us", "QQQ")]
    url = TX_QUOTE_URL.format(symbols="sh000688,hkHSI,sh000813")
    body = (quote_body("sh000688", "科创50", price=1234.5, pct=0.02)
            + quote_body("hkHSI", "恒生指数", price=25000.0, pct=-1.5)
            + quote_body("sh000813", "细分化工", price=3456.7, pct=0.5)
            + 'v_pv_none_match="1";\n')
    hook = recording_hook({url: body}).install()
    maps = bfb.realtime_maps(indices)
    assert hook.calls == [(url, "gbk", 0)]                        # ttl == TTL_REALTIME == 0
    assert maps["idx"]["sh000688"] == (1234.5, pytest.approx(0.0002))  # "0.02" → 0.0002
    assert maps["hk"]["HSI"] == (25000.0, pytest.approx(-0.015))
    assert maps["qq"]["sh000813"] == (3456.7, pytest.approx(0.005))
    assert maps["comex"] == {}
    assert set(maps) == {"idx", "hk", "qq", "comex"}


def test_realtime_maps_failure_returns_empty_maps(recording_hook):
    recording_hook().install()
    maps = bfb.realtime_maps([_item("000688", "sina_index", "sh000688")])
    assert maps == {"idx": {}, "hk": {}, "qq": {}, "comex": {}}


def test_realtime_maps_no_symbols_makes_no_call(recording_hook):
    hook = recording_hook().install()
    assert bfb.realtime_maps([_item("GC", "comex", "GC")])["idx"] == {}
    assert hook.calls == []


def test_fetch_daily_sina_index_single_tencent_page(recording_hook, after_close):
    url = bfb.tencent_page_url("sh000688")
    hook = recording_hook({url: tencent_page_json("sh000688", rows=80, end=WED, start_price=1000)}).install()
    df = bfb.fetch_daily(_item("000688", "sina_index", "sh000688"), "fb_daily:sina_index:sh000688", 1800)
    assert list(df.columns) == ["date", "close", "volume"] and len(df) == 80
    assert df["date"].is_monotonic_increasing
    assert hook.calls[0][0] == url and hook.calls[0][2] == bmd.latest_page_ttl(WED_AFTER_CLOSE)
    cached = _cache_get("fb_daily:sina_index:sh000688")
    assert cached["date"] == df["date"].tolist()


def test_fetch_daily_csindex_000813_goes_to_tencent(recording_hook, after_close):
    url = bfb.tencent_page_url("sh000813")
    hook = recording_hook({url: tencent_page_json("sh000813", rows=80, end=WED, start_price=3000)}).install()
    df = bfb.fetch_daily(_item("000813", "csindex", "000813"), "k", 1800)
    assert len(df) == 80 and hook.urls() == [url]


def test_fetch_daily_csindex_931865_unsupported(recording_hook, after_close):
    hook = recording_hook().install()
    with pytest.raises(UnsupportedOnlineError) as ei:
        bfb.fetch_daily(_item("931865", "csindex", "931865"), "k", 1800)
    assert str(ei.value).startswith(UNSUPPORTED_PREFIX) and "中证官网" in str(ei.value)
    assert hook.calls == []


def test_fetch_daily_csindex_empty_day_unsupported(recording_hook, after_close):
    recording_hook({bfb.tencent_page_url("sh000941"): page_body("sh000941", "", [])}).install()
    with pytest.raises(UnsupportedOnlineError):
        bfb.fetch_daily(_item("000941", "csindex", "000941"), "k", 1800)


def test_fetch_daily_provider_error_returns_none(recording_hook, after_close, caplog):
    recording_hook().install()  # fixture missing → ProviderError
    with caplog.at_level("WARNING"):
        assert bfb.fetch_daily(_item("000688", "sina_index", "sh000688"), "k", 1800) is None
    assert any("鱼盆在线取数失败" in r.getMessage() for r in caplog.records)
    assert _cache_get("k") is None


def test_fetch_daily_other_sources_unsupported(recording_hook, after_close):
    recording_hook().install()
    with pytest.raises(UnsupportedOnlineError):
        bfb.fetch_daily(_item("Au99.99", "sge", "Au99.99"), "k", 1800)
    with pytest.raises(UnsupportedOnlineError):
        bfb.fetch_daily(_item("IWM", "us", "IWM"), "k", 1800)


def test_fetch_daily_hk_us_symbol_mapping(recording_hook, after_close):
    hook = recording_hook({
        bfb.tencent_page_url("hkHSTECH"): tencent_page_json("hkHSTECH", rows=30, end=WED, start_price=5000),
        bfb.tencent_page_url("usQQQ.OQ"): tencent_page_json("usQQQ.OQ", rows=30, end=WED, start_price=500),
        bfb.tencent_page_url("usSPY.AM"): tencent_page_json("usSPY.AM", rows=30, end=WED, start_price=600),
    }).install()
    assert len(bfb.fetch_daily(_item("HSTECH", "hk", "HSTECH"), "a", 1)) == 30
    assert len(bfb.fetch_daily(_item("QQQ", "us", "QQQ"), "b", 1)) == 30
    assert len(bfb.fetch_daily(_item("SPY", "us", "SPY"), "c", 1)) == 30
    assert set(hook.urls()) == {bfb.tencent_page_url(s) for s in ("hkHSTECH", "usQQQ.OQ", "usSPY.AM")}


def test_fetch_daily_sina_index_short_tencent_page_falls_back_to_sina(recording_hook, after_close):
    """腾讯仅返回 1 根（bj899050 实测）→ 回落新浪 JSONP；根数足够时不触发新浪请求。"""
    tx_url = bfb.tencent_page_url("bj899050")
    sina_url = bfb.sina_kline_url("bj899050")
    var = sina_var_name("bj899050")
    sina_rows = [{"day": f"2026-09-{d:02d}", "open": "900", "high": "910", "low": "890", "close": str(900 + d),
                  "volume": str(1000 * d)} for d in range(1, 31)]
    hook = recording_hook({tx_url: tencent_page_json("bj899050", rows=1, end=WED, start_price=980)},
                          script_vars={sina_url: {var: sina_rows}}).install()
    df = bfb.fetch_daily(_item("899050", "sina_index", "bj899050"), "fb_daily:sina_index:bj899050", 1800)
    assert len(df) == 30 and df["date"].iloc[-1] == "2026-09-30" and df["close"].iloc[-1] == 930
    assert hook.urls() == [tx_url] and hook.script_calls == [(sina_url, [var], bmd.TTL_SCRIPT)]

    hook = recording_hook({bfb.tencent_page_url("sh000016"):
                           tencent_page_json("sh000016", rows=bfb.MIN_HISTORY_ROWS, end=WED, start_price=2500)}).install()
    assert len(bfb.fetch_daily(_item("000016", "sina_index", "sh000016"), "k", 1800)) == bfb.MIN_HISTORY_ROWS
    assert hook.script_calls == []


def test_fetch_daily_comex_via_sina_futures_jsonp(recording_hook, after_close):
    var = futures_var_name("GC")
    url = SINA_FUTURES_URL.format(var=var, sym="GC")
    hook = recording_hook(script_vars={url: {var: [
        {"date": "2026-10-14", "open": "4000", "high": "4010", "low": "3990", "close": "4005", "volume": "123"},
        {"date": "2026-10-13", "open": "3990", "high": "4001", "low": "3980", "close": "3995", "volume": "100"},
    ]}}).install()
    df = bfb.fetch_daily(_item("GC", "comex", "GC"), "fb_daily:comex:GC", 1800)
    assert df["date"].tolist() == ["2026-10-13", "2026-10-14"] and df["close"].iloc[-1] == 4005
    assert hook.script_calls == [(url, [var], bmd.TTL_SCRIPT)]


def test_fetch_daily_drop_live_bar_only_for_a_share(recording_hook, monkeypatch):
    monkeypatch.setattr(bmd, "_now_cn", lambda: WED_IN_SESSION)
    recording_hook({
        bfb.tencent_page_url("sh000688"): tencent_page_json("sh000688", rows=30, end=WED, start_price=1000),
        bfb.tencent_page_url("hkHSI"): tencent_page_json("hkHSI", rows=30, end=WED, start_price=25000),
    }).install()
    a_share = bfb.fetch_daily(_item("000688", "sina_index", "sh000688"), "a", 1)
    hk = bfb.fetch_daily(_item("HSI", "hk", "HSI"), "b", 1)
    assert a_share["date"].iloc[-1] == "2026-10-20"
    assert hk["date"].iloc[-1] == "2026-10-21"


# ---------------------------------------------------------------- fish_basin_service 集成（ak=None）
def test_service_browser_path_and_unsupported_postpass(recording_hook, after_close, monkeypatch):
    monkeypatch.setattr(fbs, "ak", None)
    monkeypatch.setattr(fbs, "IS_PYODIDE", True)
    monkeypatch.setattr(concurrency, "IS_PYODIDE", True)
    indices = [_item("000688", "sina_index", "sh000688", "科创50"),
               _item("932000", "csindex", "932000", "中证2000"),
               _item("000813", "csindex", "000813", "细分化工")]
    quote_url = TX_QUOTE_URL.format(symbols="sh000688,sh000813")
    recording_hook({
        bfb.tencent_page_url("sh000688"): tencent_page_json("sh000688", rows=80, end=WED, start_price=1000),
        bfb.tencent_page_url("sh000813"): tencent_page_json("sh000813", rows=80, end=WED, start_price=3000),
        quote_url: quote_body("sh000688", "科创50", price=1050.0, pct=1.0) + quote_body("sh000813", "细分化工", 3100.0, 0.3),
    }).install()
    payload = fbs.FishBasinService().evaluate(indices, board="index_test", force_refresh=True)
    by_code = {r["code"]: r for r in payload["results"]}
    assert by_code["000688"]["status"] in ("YES", "NO") and by_code["000688"]["is_realtime"] is True
    assert by_code["000813"]["status"] in ("YES", "NO")
    assert by_code["932000"]["status"] is None and by_code["932000"]["error"].startswith(UNSUPPORTED_PREFIX)
    assert payload["unsupported"] == [{"code": "932000", "name": "中证2000", "reason": by_code["932000"]["error"]}]
    assert payload["realtime_count"] == 2


def test_service_native_has_no_unsupported_key(recording_hook, after_close, monkeypatch):
    monkeypatch.setattr(fbs, "ak", None)   # 在线取数，但 IS_PYODIDE 为 False（原生）
    indices = [_item("932000", "csindex", "932000", "中证2000")]
    recording_hook().install()
    payload = fbs.FishBasinService().evaluate(indices, board="index_native", force_refresh=True)
    assert "unsupported" not in payload
    assert payload["results"][0]["error"].startswith(UNSUPPORTED_PREFIX)
