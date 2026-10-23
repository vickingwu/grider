"""providers 纯解析/URL 构造单元测试（无网络）。"""
import json
import math
import os

import pytest

from app.services.providers import eastmoney, sina_jsonp, tencent
from app.services.providers.errors import ProviderError, UnsupportedOnlineError, UNSUPPORTED_PREFIX
from tests.browser.synth import quote_body, tencent_page_json

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures", "browser")

# 取自 TASK\probe_cors.txt 的 GBK 行情样本（已解码），补齐到 ≥ 33 字段
_PROBE_QUOTE_PREFIX = ("1~沪深300ETF华泰柏瑞~510300~4.229~4.240~4.229~2739338~1318808~1420364~4.229~72~4.228~1253~4.227~9162"
                       "~4.226~9368~4.225~5886~4.230~77483~4.231~85315~4.232~2")
_PROBE_QUOTE_FIELDS = _PROBE_QUOTE_PREFIX.split("~")            # 25 个字段 (0..24)
_PROBE_QUOTE_FIELDS += ["0", "0", "0", "0", "0"]                 # 25..29
_PROBE_QUOTE_FIELDS += ["20261021150000", "-0.011", "-0.26"]     # 30 时间戳, 31 涨跌额, 32 涨跌幅(%)
_PROBE_QUOTE_FIELDS += ["4.240", "4.211", "4.229/2739338/1157036534", "2739338", "115704", "0.00", "0", "", "4.240", "0.69"]
_PROBE_QUOTE = 'v_sh510300="' + "~".join(_PROBE_QUOTE_FIELDS) + '";\n'


# ---------------------------------------------------------------- tencent
def test_to_tencent_symbol():
    assert tencent.to_tencent_symbol("510300", "ETF", "XSHG") == "sh510300"
    assert tencent.to_tencent_symbol("159915", "ETF", "XSHE") == "sz159915"
    assert tencent.to_tencent_symbol("000300", "INDEX", "XSHG") == "sh000300"
    assert tencent.to_tencent_symbol("399006", "INDEX", "XSHE") == "sz399006"
    assert tencent.to_tencent_symbol("899050", "INDEX", "XSHG") == "bj899050"


def test_parse_kline_json_order_and_dividend_row():
    body = tencent_page_json("sh510300", fq="qfq", rows=5, dividend_at=2)
    raw = json.loads(body)["data"]["sh510300"]["qfqday"]
    rows = tencent.parse_kline_json(body, "sh510300", "qfq")
    assert len(rows) == 5
    assert len(raw[2]) == 7 and isinstance(raw[2][6], dict)
    for src, row in zip(raw, rows):
        assert row.date == src[0]
        assert row.open == float(src[1])
        assert row.close == float(src[2])   # 索引 2 = close
        assert row.high == float(src[3])    # 索引 3 = high
        assert row.low == float(src[4])
        assert row.volume == float(src[5])


def test_parse_kline_json_non_numeric_to_nan_and_code_error():
    body = json.dumps({"code": 0, "data": {"sh510300": {"day": [["2026-01-05", "x", "4.2", "4.3", "4.1", "100"]]}}})
    rows = tencent.parse_kline_json(body, "sh510300", "")
    assert math.isnan(rows[0].open) and rows[0].close == 4.2
    with pytest.raises(ProviderError):
        tencent.parse_kline_json(json.dumps({"code": -1, "msg": "param error"}), "sh510300", "")
    with pytest.raises(ProviderError):
        tencent.parse_kline_json("<html>nope</html>", "sh510300", "")
    assert tencent.parse_kline_json(json.dumps({"code": 0, "data": {"sh510300": {"day": []}}}), "sh510300", "") == []


def test_parse_mkline_json():
    body = json.dumps({"code": 0, "data": {"sh510300": {"m60": [
        ["202610211030", "4.229", "4.231", "4.235", "4.220", "123456"],
        ["202610211130", "4.231", "4.228", "4.233", "4.221", "98765"],
    ]}}})
    rows = tencent.parse_mkline_json(body, "sh510300", "m60")
    assert [r.time for r in rows] == ["202610211030", "202610211130"]
    assert rows[0].close == 4.231 and rows[0].high == 4.235


def test_parse_quote_probe_sample_gbk_fields():
    quotes = tencent.parse_quote(_PROBE_QUOTE)
    q = quotes["sh510300"]
    assert q.name == "沪深300ETF华泰柏瑞"
    assert q.price == 4.229
    assert q.timestamp == "20261021150000"
    assert q.pct == pytest.approx(-0.26 / 100.0)


def test_parse_quote_pct_is_fraction_and_misses_yield_no_entry():
    text = quote_body("sh000813", "细分化工", price=3456.78, pct=0.02) + 'v_pv_none_match="1";\n' + 'v_sh999999="";\n'
    quotes = tencent.parse_quote(text)
    assert set(quotes) == {"sh000813"}
    assert quotes["sh000813"].pct == pytest.approx(0.0002)  # "0.02" → 0.0002
    assert quotes.get("sh999999") is None
    assert tencent.parse_quote('v_pv_none_match="1";') == {}
    assert tencent.parse_quote("") == {}


def test_parse_quote_requires_33_fields():
    assert tencent.parse_quote('v_sh510300="1~a~510300~4.2";') == {}


def test_parse_quote_gbk_roundtrip():
    text = quote_body("sh510300", "沪深300ETF华泰柏瑞").encode("gbk").decode("gbk")
    assert tencent.parse_quote(text)["sh510300"].name == "沪深300ETF华泰柏瑞"


# ---------------------------------------------------------------- eastmoney
def test_secid_for():
    assert eastmoney.secid_for("sh510300") == "1.510300"
    assert eastmoney.secid_for("sz159915") == "0.159915"
    assert eastmoney.secid_for("bj899050") is None


def test_em_kline_url_fields():
    url = eastmoney.EM_KLINE_URL.format(secid="1.510300", fqt=1)
    assert "klt=101" in url and "fqt=1" in url and "secid=1.510300" in url
    assert "fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61" in url


def test_parse_em_kline_synthetic_fixture():
    with open(os.path.join(FIXTURES, "em_kline_synthetic.json"), "r", encoding="utf-8") as f:
        text = f.read()
    df = eastmoney.parse_em_kline(text)
    assert list(df.columns) == ["date", "open", "high", "low", "close", "volume", "amount", "change_pct"]
    assert len(df) == 4
    first = df.iloc[0]
    assert first["date"] == "2025-10-09"
    assert first["open"] == 4.631 and first["close"] == 4.693 and first["high"] == 4.713 and first["low"] == 4.627
    assert first["volume"] == 89709            # 手，不乘 100
    assert first["amount"] == 415842560.0      # 真实成交额
    assert first["change_pct"] == 1.34         # 第 8 个字段 pct


def test_parse_em_kline_null_data_raises():
    with pytest.raises(ProviderError):
        eastmoney.parse_em_kline(json.dumps({"rc": 0, "data": None}))
    with pytest.raises(ProviderError):
        eastmoney.parse_em_kline(json.dumps({"rc": 1, "data": {"klines": []}}))
    with pytest.raises(ProviderError):
        eastmoney.parse_em_kline("not json")


def test_pingzhong_url_deterministic():
    assert eastmoney.PINGZHONG_URL.format(code="110011") == "https://fund.eastmoney.com/pingzhongdata/110011.js"


def test_extract_js_vars_pingzhong_like_text():
    text = ('\ufeff/** * 测试数据 */var ishb=false;/*基金或股票信息*/var fS_name = "易方达优质精选混合(QDII)";'
            'var fS_code = "110011";var Data_netWorthTrend = [{"x":1697040000000,"y":1.2345,"equityReturn":0.5,'
            '"unitMoney":""},{"x":1697126400000,"y":1.2400,"equityReturn":0.45,"unitMoney":""}];'
            'var Data_nothing = null;')
    out = eastmoney.extract_js_vars(text, ["fS_name", "Data_netWorthTrend", "Data_nothing"])
    assert out["fS_name"] == "易方达优质精选混合(QDII)"
    assert out["Data_netWorthTrend"][0]["y"] == 1.2345
    assert out["Data_nothing"] is None


def test_extract_js_vars_paren_wrapper_and_missing_name():
    text = 'var _k_sh510300=([{"day":"2026-10-13","open":"4.313","close":"4.345","volume":"1081547864"}]);'
    out = eastmoney.extract_js_vars(text, ["_k_sh510300"])
    assert out["_k_sh510300"][0]["day"] == "2026-10-13"
    with pytest.raises(ProviderError):
        eastmoney.extract_js_vars(text, ["_k_sz159915"])


def test_extract_js_vars_html_notfound_body_fails_cleanly():
    html = ("<!DOCTYPE html><html><head><title>页面未找到 - 天天基金网</title></head>"
            "<body><div class='notfound'>抱歉，您访问的页面不存在</div></body></html>")
    with pytest.raises(ProviderError):
        eastmoney.extract_js_vars(html, ["fS_name", "Data_netWorthTrend"])


def test_parse_networth_trend_fixed_cn_tz():
    assert eastmoney.em_epoch_ms_to_date(1697040000000) == "2023-10-12"
    df = eastmoney.parse_networth_trend([
        {"x": 1697126400000, "y": 1.24},
        {"x": 1697040000000, "y": 1.2345},
        {"x": 1697212800000, "y": None},
    ])
    assert df["date"].tolist() == ["2023-10-12", "2023-10-13"]
    assert (df["open"] == df["close"]).all() and (df["high"] == df["low"]).all()
    assert (df["volume"] == 0).all() and (df["amount"] == 0.0).all()
    assert eastmoney.parse_networth_trend([]) is None
    assert eastmoney.parse_networth_trend(None) is None


# ---------------------------------------------------------------- sina_jsonp
def test_sina_urls_and_var_names():
    assert sina_jsonp.sina_var_name("sh510300") == "_k_sh510300"
    assert sina_jsonp.futures_var_name("GC") == "_f_GC"
    url = sina_jsonp.SINA_KLINE_URL.format(var="_k_sh510300", symbol="sh510300")
    assert "var%20_k_sh510300=" in url and "datalen=1970" in url and "scale=240" in url
    furl = sina_jsonp.SINA_FUTURES_URL.format(var="_f_GC", sym="GC")
    assert "var%20_f_GC=" in furl and "symbol=GC" in furl


def test_parse_sina_kline_and_futures():
    rows = [
        {"day": "2026-10-14", "open": "4.35", "high": "4.40", "low": "4.30", "close": "4.38", "volume": "1000"},
        {"day": "2026-10-13", "open": "4.313", "high": "4.384", "low": "4.286", "close": "4.345", "volume": "1081547864"},
    ]
    df = sina_jsonp.parse_sina_kline(rows)
    assert df["date"].tolist() == ["2026-10-13", "2026-10-14"]
    assert df["volume"].iloc[0] == 1081547864  # 股，不乘系数
    assert df["amount"].iloc[1] == pytest.approx((4.35 + 4.38 + 4.40 + 4.30) / 4 * 1000)
    fut = sina_jsonp.parse_sina_futures([
        {"date": "2026-10-13", "open": "4000", "high": "4010", "low": "3990", "close": "4005", "volume": "123"},
    ])
    assert list(fut.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert fut["close"].iloc[0] == 4005
    assert sina_jsonp.parse_sina_kline(None) is None
    assert sina_jsonp.parse_sina_futures([]) is None


# ---------------------------------------------------------------- errors
def test_error_types():
    assert issubclass(UnsupportedOnlineError, ValueError)
    assert str(UnsupportedOnlineError(UNSUPPORTED_PREFIX)).startswith("在线版暂不支持")
    assert not issubclass(ProviderError, ValueError)
