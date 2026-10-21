"""worker 的唯一入口：dispatch(method, path, query_string, body_json) → {status, content_type, body}。

永不抛异常：引擎异常转为 500 JSON；超限请求转为 404/413 JSON。
CLI：python -m app.browser.dispatch --fixtures DIR --request FILE.json
（顺序：install_fixture_hooks → set_provider_class(BrowserMarketData) → dispatch；本模块不在模块级导入 app.routes）
"""
import argparse
import json
import sys
import traceback
from typing import Optional
from urllib.parse import urlencode

from app.browser.app_factory import INTERNAL_ERROR_MESSAGE, NOT_FOUND_MESSAGE, create_browser_app

_ALLOWED_METHODS = ("GET", "POST")
_MAX_PATH_LEN = 2048
_MAX_QUERY_LEN = 4096
_MAX_BODY_LEN = 1_048_576
TOO_LARGE_MESSAGE = "请求过大"

_client = None


def _get_client():
    """惰性创建 test_client（首次调用才导入并注册路由）。"""
    global _client
    if _client is None:
        _client = create_browser_app().test_client()
    return _client


def _error(status: int, message: str, detail: Optional[str] = None) -> dict:
    """统一错误信封：同时带 error 与 message，兼容前端 api.js 的两种读取方式。"""
    body = {"success": False, "error": message, "message": message}
    if detail:
        body["detail"] = detail
    return {"status": status, "content_type": "application/json",
            "body": json.dumps(body, ensure_ascii=False)}


def dispatch(method: str, path: str, query_string: str = "", body_json: Optional[str] = None) -> dict:
    """返回 {"status": int, "content_type": str, "body": str}。永不抛异常（引擎错误转为 500 JSON）。"""
    method = (method or "GET").upper()
    path = path or ""
    query_string = query_string or ""
    if method not in _ALLOWED_METHODS or not path.startswith("/api/") or ".." in path or len(path) > _MAX_PATH_LEN:
        return _error(404, NOT_FOUND_MESSAGE)
    if len(query_string) > _MAX_QUERY_LEN or (body_json is not None and len(body_json) > _MAX_BODY_LEN):
        return _error(413, TOO_LARGE_MESSAGE)
    try:
        kwargs = {"query_string": query_string}
        if body_json is not None:
            kwargs["data"] = body_json
            kwargs["content_type"] = "application/json"
        resp = _get_client().open(path, method=method, **kwargs)
        return {"status": resp.status_code, "content_type": resp.content_type,
                "body": resp.get_data(as_text=True)}
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return _error(500, INTERNAL_ERROR_MESSAGE, detail=f"{type(e).__name__}: {e}")


def dispatch_json(method: str, path: str, query_string: str = "", body_json: Optional[str] = None) -> str:
    """worker 调用入口：返回 JSON 字符串，避免 PyProxy 转换/销毁（JS 侧直接 JSON.parse）。"""
    return json.dumps(dispatch(method, path, query_string, body_json), ensure_ascii=False)


def _query_to_string(query) -> str:
    if query is None:
        return ""
    if isinstance(query, dict):
        return urlencode(query)
    return str(query)


def _main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="在线引擎分发器 CLI（fixture 驱动，无网络）")
    parser.add_argument("--fixtures", required=True, help="fixture 目录（含 index.json）")
    parser.add_argument("--request", required=True, help="请求文件 {method, path, query, body}")
    args = parser.parse_args(argv)

    from app.browser.testing import install_fixture_hooks
    from app.services.data_service import set_provider_class
    from app.services.providers.browser_market_data import BrowserMarketData

    install_fixture_hooks(args.fixtures)
    set_provider_class(BrowserMarketData)

    with open(args.request, "r", encoding="utf-8") as f:
        req = json.load(f)
    body = req.get("body")
    body_json = json.dumps(body, ensure_ascii=False) if body is not None else None
    result = dispatch(req.get("method", "GET"), req["path"], _query_to_string(req.get("query")), body_json)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] < 500 else 1


if __name__ == "__main__":
    sys.exit(_main())
