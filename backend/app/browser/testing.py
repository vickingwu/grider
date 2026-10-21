"""fixture 驱动的取数钩子（原生单元测试、CLI 与 Pyodide 对比测试共用）。

index.json 结构：{"captured_at": "YYYY-MM-DD", "entries": {"<精确 URL>": "<正文文件名>"}}。
未知 URL → ProviderError("fixture missing: …")，以证明没有隐藏的网络依赖。
"""
import json
import os
from typing import Any, Dict, List, Optional

from app.services.providers import browser_market_data as bmd
from app.services.providers.eastmoney import extract_js_vars
from app.services.providers.errors import ProviderError

INDEX_FILENAME = "index.json"


def load_fixture_index(index_dir: str) -> Dict[str, Any]:
    """读取 index.json。"""
    with open(os.path.join(index_dir, INDEX_FILENAME), "r", encoding="utf-8") as f:
        return json.load(f)


def install_fixture_hooks(index_dir: str) -> Dict[str, Any]:
    """安装 http_get_text / fetch_script_vars 的 fixture 实现；返回读取到的 index（含 captured_at）。"""
    index = load_fixture_index(index_dir)
    entries: Dict[str, str] = index.get("entries") or {}

    def _read(url: str) -> bytes:
        filename = entries.get(url)
        if filename is None:
            raise ProviderError(f"fixture missing: {url}")
        with open(os.path.join(index_dir, filename), "rb") as f:
            return f.read()

    def _http_get_text(url: str, encoding: Optional[str], ttl: int) -> str:
        return bmd.decode_response_bytes(_read(url), encoding)

    def _fetch_script_vars(url: str, names: List[str], ttl: int) -> Dict[str, Any]:
        text = bmd.decode_response_bytes(_read(url), bmd.script_encoding_for(url))
        return extract_js_vars(text, names)

    bmd.set_http_get_text(_http_get_text)
    bmd.set_fetch_script_vars(_fetch_script_vars)
    return index
