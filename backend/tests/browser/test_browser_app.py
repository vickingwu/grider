"""app.browser：路由集合一致性、dispatch 错误信封、fixture 驱动的端到端请求。"""
import importlib
import json
import os
import subprocess
import sys
from datetime import date, datetime

import pytest

from app import create_app
from app.browser.app_factory import create_browser_app
from app.browser.dispatch import dispatch, dispatch_json
from app.browser.testing import install_fixture_hooks
from app.services.data_service import set_provider_class
from app.services.providers import browser_market_data as bmd
from app.services.providers.browser_market_data import CN_TZ, BrowserMarketData
from app.services.providers.tencent import TX_KLINE_URL, TX_QUOTE_URL
from tests.browser.synth import quote_body, tencent_page_json

WED = date(2026, 10, 21)
WED_AFTER_CLOSE = datetime(2026, 10, 21, 16, 0, tzinfo=CN_TZ)
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# app.browser 包把 dispatch 函数导出到包命名空间，故按模块名取子模块
dispatch_mod = importlib.import_module("app.browser.dispatch")


def _page_url(symbol: str, fq: str = "") -> str:
    return TX_KLINE_URL.format(symbol=symbol, start="", end="", fq=fq)


def write_synthetic_fixtures(dir_path: str, end: date = WED) -> str:
    """写出 510300 / sh000001 / 行情 的合成 fixture 目录（index.json + 正文文件）。"""
    files = {
        "sh510300_p1.json": (_page_url("sh510300"), tencent_page_json("sh510300", rows=400, end=end).encode("utf-8")),
        "sh000001_p1.json": (_page_url("sh000001"), tencent_page_json("sh000001", rows=400, end=end,
                                                                        start_price=3300).encode("utf-8")),
        "q_sh510300.txt": (TX_QUOTE_URL.format(symbols="sh510300"),
                           quote_body("sh510300", "沪深300ETF华泰柏瑞").encode("gbk")),
    }
    entries = {}
    for filename, (url, body) in files.items():
        with open(os.path.join(dir_path, filename), "wb") as f:
            f.write(body)
        entries[url] = filename
    with open(os.path.join(dir_path, "index.json"), "w", encoding="utf-8") as f:
        json.dump({"captured_at": end.isoformat(), "entries": entries}, f, ensure_ascii=False, indent=2)
    return dir_path


@pytest.fixture
def browser_env(tmp_path, monkeypatch):
    """set_provider_class + fixture 钩子（在路由导入之前）+ info_routes 重绑定 + 新鲜 test_client。"""
    monkeypatch.setattr(bmd, "_now_cn", lambda: WED_AFTER_CLOSE)
    index_dir = write_synthetic_fixtures(str(tmp_path))
    set_provider_class(BrowserMarketData)
    install_fixture_hooks(index_dir)
    import app.routes.info_routes as info_routes
    if not isinstance(info_routes._data_service, BrowserMarketData):
        monkeypatch.setattr(info_routes, "_data_service", BrowserMarketData())
    monkeypatch.setattr(dispatch_mod, "_client", None)
    return index_dir


def _api_rules(app):
    return {(r.rule, tuple(sorted(r.methods - {"HEAD", "OPTIONS"}))) for r in app.url_map.iter_rules()
            if r.rule.startswith("/api")}


def test_api_rule_set_equals_create_app():
    assert _api_rules(create_browser_app()) == _api_rules(create_app())
    assert len(_api_rules(create_browser_app())) >= 10


def test_dispatch_guard_envelopes(browser_env):
    for method, path, qs, body in [
        ("DELETE", "/api/version", "", None),
        ("GET", "/nope", "", None),
        ("GET", "/api/../etc", "", None),
        ("GET", "/api/" + "a" * 2100, "", None),
    ]:
        out = dispatch(method, path, qs, body)
        assert out["status"] == 404 and out["content_type"] == "application/json"
        payload = json.loads(out["body"])
        assert payload == {"success": False, "error": "未知接口", "message": "未知接口"}

    out = dispatch("GET", "/api/version", "q=" + "x" * 5000)
    assert out["status"] == 413 and json.loads(out["body"])["message"] == "请求过大"
    out = dispatch("POST", "/api/grid/analyze", "", "{" + " " * 1_048_576 + "}")
    assert out["status"] == 413 and json.loads(out["body"])["error"] == "请求过大"


def test_dispatch_engine_exception_becomes_500(browser_env, monkeypatch):
    class _Boom:
        def open(self, *a, **k):
            raise RuntimeError("engine exploded")

    monkeypatch.setattr(dispatch_mod, "_get_client", lambda: _Boom())
    out = dispatch("GET", "/api/version")
    assert out["status"] == 500
    payload = json.loads(out["body"])
    assert payload["success"] is False and payload["error"] == payload["message"] == "在线引擎内部错误，请刷新页面重试"
    assert "RuntimeError" in payload["detail"]


def test_unknown_api_path_is_json_404(browser_env):
    out = dispatch("GET", "/api/nonexistent")
    assert out["status"] == 404
    payload = json.loads(out["body"])
    assert payload["success"] is False and payload["message"] == "未知接口"


def test_method_not_allowed_is_json_405(browser_env):
    client = create_browser_app().test_client()
    resp = client.put("/api/version")
    assert resp.status_code == 405 and resp.is_json
    assert resp.get_json() == {"success": False, "message": "Method Not Allowed", "error": "Method Not Allowed"}


def test_malformed_json_body_is_json_400(browser_env):
    out = dispatch("POST", "/api/grid/analyze", "", "{not json")
    assert out["status"] == 400
    payload = json.loads(out["body"])
    assert payload["success"] is False and payload["message"] == "Bad Request"


def test_route_exception_is_json_500():
    app = create_browser_app()

    @app.route("/api/_boom")
    def _boom():
        raise ValueError("x")

    resp = app.test_client().get("/api/_boom")
    assert resp.status_code == 500
    assert resp.get_json()["message"] == "在线引擎内部错误，请刷新页面重试"


def test_version_and_capital(browser_env):
    out = json.loads(dispatch_json("GET", "/api/version"))
    assert out["status"] == 200
    body = json.loads(out["body"])
    assert body["success"] is True and "version" in body["data"]
    out = dispatch("GET", "/api/info/capital")
    assert out["status"] == 200 and json.loads(out["body"])["success"] is True


def test_grid_analyze_with_synthetic_fixtures(browser_env):
    body = json.dumps({"etfCode": "510300", "totalCapital": 100000, "gridType": "等比",
                       "riskPreference": "均衡", "adjustmentCoefficient": 1.0}, ensure_ascii=False)
    out = dispatch("POST", "/api/grid/analyze", "", body)
    assert out["status"] == 200, out["body"]
    payload = json.loads(out["body"])
    assert payload["success"] is True
    data = payload["data"]
    assert data["etf_info"]["code"] == "510300" and data["etf_info"]["name"] == "沪深300ETF"
    assert data["etf_info"]["date"] == "2026-10-21"
    assert data["grid_strategy"]["grid_config"]["count"] > 0


def test_info_route_uses_browser_provider(browser_env):
    import app.routes.info_routes as info_routes
    assert isinstance(info_routes._data_service, BrowserMarketData)
    out = dispatch("GET", "/api/info/510300")
    assert out["status"] == 200
    assert json.loads(out["body"])["data"]["name"] == "沪深300ETF"


def test_fixture_hook_unknown_url_raises(browser_env):
    with pytest.raises(bmd.ProviderError, match="fixture missing"):
        bmd.http_get_text("https://example.invalid/none", None, 0)


def test_dispatch_cli_with_fixtures(browser_env, tmp_path):
    req_path = os.path.join(str(tmp_path), "req_version.json")
    with open(req_path, "w", encoding="utf-8") as f:
        json.dump({"method": "GET", "path": "/api/version", "query": {}, "body": None}, f)
    env = {**os.environ, "LOG_TO_CONSOLE": "false", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, "-m", "app.browser.dispatch", "--fixtures", browser_env,
                           "--request", req_path], cwd=BACKEND_DIR, capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=180)
    assert proc.returncode == 0, proc.stderr[-2000:]
    out = json.loads(proc.stdout)
    assert out["status"] == 200 and json.loads(out["body"])["success"] is True
