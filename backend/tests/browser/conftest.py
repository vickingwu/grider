"""tests/browser 公共夹具：隔离全局钩子/provider/缓存，去掉节流与重试等待。"""
import sys

import pytest

from app.services import data_service
from app.services.data_service import _CACHE, _CACHE_LOCK
from app.services.providers import browser_market_data as bmd


def _clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


@pytest.fixture(autouse=True)
def _reset_global_hooks(monkeypatch):
    """NIT 11：每个测试后恢复三个 setter 与 info_routes._data_service；另外清空进程缓存、关闭 sleep。"""
    info_routes = sys.modules.get("app.routes.info_routes")
    saved_service = getattr(info_routes, "_data_service", None) if info_routes else None

    monkeypatch.setattr(bmd, "TX_BACKOFF", (0, 0))
    monkeypatch.setattr(bmd, "TX_MIN_INTERVAL", 0)
    monkeypatch.setattr(bmd, "EM_MIN_INTERVAL", 0)
    bmd._LAST_CALL_AT.clear()
    _clear_cache()
    yield
    data_service.set_provider_class(None)
    bmd.set_http_get_text(None)
    bmd.set_fetch_script_vars(None)
    bmd.set_cache_evict(None)
    if info_routes is not None and saved_service is not None:
        info_routes._data_service = saved_service
    _clear_cache()


class RecordingHook:
    """可编程的 http_get_text / fetch_script_vars / cache_evict 假钩子：按 URL 返回正文并记录每次调用的 ttl。

    sequences={url: [body | Exception, ...]}：同一 URL 第 k 次调用返回第 k 项（Exception 实例则 raise），
    用尽后回落 bodies。evicted 记录 cache_evict 收到的 URL 顺序。
    """

    def __init__(self, bodies=None, script_vars=None, sequences=None):
        self.bodies = dict(bodies or {})
        self.script_vars = dict(script_vars or {})
        self.sequences = {url: list(items) for url, items in (sequences or {}).items()}
        self.calls = []          # (url, encoding, ttl)
        self.script_calls = []   # (url, names, ttl)
        self.evicted = []        # cache_evict(url) 顺序
        self.fail_urls = set()

    def http_get_text(self, url, encoding, ttl):
        self.calls.append((url, encoding, ttl))
        if url in self.fail_urls:
            raise bmd.ProviderError(f"forced failure: {url}")
        queue = self.sequences.get(url)
        if queue:
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if url not in self.bodies:
            raise bmd.ProviderError(f"fixture missing: {url}")
        return self.bodies[url]

    def fetch_script_vars(self, url, names, ttl):
        self.script_calls.append((url, list(names), ttl))
        if url in self.fail_urls:
            raise bmd.ProviderError(f"forced failure: {url}")
        if url not in self.script_vars:
            raise bmd.ProviderError(f"fixture missing: {url}")
        return {n: self.script_vars[url].get(n) for n in names}

    def evict(self, url):
        self.evicted.append(url)

    def install(self):
        bmd.set_http_get_text(self.http_get_text)
        bmd.set_fetch_script_vars(self.fetch_script_vars)
        bmd.set_cache_evict(self.evict)
        return self

    def urls(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def recording_hook():
    return RecordingHook
