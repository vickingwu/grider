"""worker 启动引导：init(bridge) 注入 JS 桥取数钩子、注册 BrowserMarketData、预热 /api/version。

bridge 为 JS 对象（Pyodide 的 JsProxy），需提供：
- httpGetBytes(url, timeoutMs, ttlSeconds) -> Uint8Array（非 200/网络错误/超时抛 JS 异常）
- loadScriptVars(url, namesJson, ttlSeconds) -> JSON 字符串 {name: value}
- evictCache(url) -> bool（可选：驱逐该 URL 的 Map/IndexedDB 缓存，供空页重试前调用）

本模块不导入 pyodide（原生可导入，供 import-hygiene 测试与 CLI 使用）；JS 异常作为普通 Exception
被包装为 ProviderError。init 幂等：二次调用直接返回首次结果。
"""
import json
import logging
import sys
from typing import Any, Dict, List, Optional

import pandas as pd

from app.constants import APP_VERSION
from app.services.data_service import set_provider_class
from app.services.providers import browser_market_data as bmd
from app.services.providers.browser_market_data import BrowserMarketData
from app.services.providers.errors import ProviderError

_INFO_JSON: Optional[str] = None


def _make_http_get_text(bridge: Any):
    def _http_get_text(url: str, encoding: Optional[str], ttl: int) -> str:
        try:
            raw = bridge.httpGetBytes(url, bmd.HTTP_TIMEOUT_MS, int(ttl))
            data = raw.to_bytes() if hasattr(raw, "to_bytes") else bytes(raw)
        except Exception as e:  # noqa: BLE001  含 JsException（其基类为 Exception）
            raise ProviderError(str(e)) from e
        return bmd.decode_response_bytes(data, encoding)

    return _http_get_text


def _make_fetch_script_vars(bridge: Any):
    def _fetch_script_vars(url: str, names: List[str], ttl: int) -> Dict[str, Any]:
        try:
            text = bridge.loadScriptVars(url, json.dumps(list(names)), int(ttl))  # 传 JSON 字符串，避免 PyProxy 泄漏
        except Exception as e:  # noqa: BLE001
            raise ProviderError(str(e)) from e
        try:
            values = json.loads(str(text))
        except ValueError as e:
            raise ProviderError(f"loadScriptVars 返回非 JSON: {e}") from e
        if not isinstance(values, dict):
            raise ProviderError("loadScriptVars 返回值不是对象")
        missing = [n for n in names if n not in values]
        if missing:
            raise ProviderError(f"脚本中缺少变量 {', '.join(missing)}")
        return values

    return _fetch_script_vars


def _rebind_info_routes() -> None:
    """info_routes._data_service 在导入时绑定；若已用本地类导入过，则换成浏览器实现。"""
    module = sys.modules.get("app.routes.info_routes")
    if module is None:
        return
    if not isinstance(getattr(module, "_data_service", None), BrowserMarketData):
        module._data_service = BrowserMarketData()


def init(bridge: Any) -> str:
    """一次性初始化；返回 JSON 字符串 {"version", "python", "pandas"} 供 worker 的 ready 消息使用。"""
    global _INFO_JSON
    if _INFO_JSON is not None:
        return _INFO_JSON

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s")
    bmd.set_http_get_text(_make_http_get_text(bridge))
    bmd.set_fetch_script_vars(_make_fetch_script_vars(bridge))
    evict = getattr(bridge, "evictCache", None)  # 旧 worker 无此方法时保持 no-op
    if evict is not None:
        bmd.set_cache_evict(lambda url: evict(url))
    set_provider_class(BrowserMarketData)
    _rebind_info_routes()

    from app.browser.dispatch import dispatch  # 此时才导入并注册路由，确保 info_routes 用浏览器实现
    _rebind_info_routes()
    warm = dispatch("GET", "/api/version")
    if warm["status"] != 200:
        raise RuntimeError(f"在线引擎预热失败: GET /api/version -> {warm['status']} {warm['body']}")

    _INFO_JSON = json.dumps({"version": APP_VERSION, "python": sys.version, "pandas": pd.__version__},
                            ensure_ascii=False)
    return _INFO_JSON
