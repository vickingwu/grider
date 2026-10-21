"""导入卫生：在屏蔽 akshare / flask 扩展 / requests / yaml 的子进程中，bootstrap.init 与 dispatch 仍可工作。

模拟 Pyodide 精简环境（只有 flask + pandas + 标准库）。
"""
import json
import os
import subprocess
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BLOCKED = ["akshare", "flask_sqlalchemy", "flask_migrate", "flask_jwt_extended", "flask_cors", "dotenv",
           "requests", "yaml"]

_SCRIPT = r'''
import importlib.abc, json, sys

BLOCKED = set(%(blocked)s)

class _Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        root = fullname.split(".")[0]
        if root in BLOCKED:
            raise ImportError(f"blocked for hygiene test: {fullname}")
        return None

sys.meta_path.insert(0, _Blocker())
for name in list(sys.modules):
    if name.split(".")[0] in BLOCKED:
        del sys.modules[name]

from app.browser import bootstrap
from app.browser.dispatch import dispatch_json


class FakeBridge:
    def httpGetBytes(self, *args):
        raise AssertionError("bridge must not be called during init")

    def loadScriptVars(self, *args):
        raise AssertionError("bridge must not be called during init")


info = json.loads(bootstrap.init(FakeBridge()))
assert bootstrap.init(FakeBridge()) == json.dumps(info, ensure_ascii=False), "init must be idempotent"
assert set(info) == {"version", "python", "pandas"}, info

out = json.loads(dispatch_json("GET", "/api/version"))
assert out["status"] == 200, out
assert json.loads(out["body"])["success"] is True

from app.routes import info_routes
from app.services.providers.browser_market_data import BrowserMarketData
assert isinstance(info_routes._data_service, BrowserMarketData), type(info_routes._data_service)

from app.services import data_service, fish_basin_service
assert data_service.ak is None and fish_basin_service.ak is None

# 钩子包装：桥异常 → ProviderError
from app.services.providers import browser_market_data as bmd
from app.services.providers.errors import ProviderError
try:
    bmd.http_get_text("https://example.invalid", None, 0)
except ProviderError:
    pass
else:
    raise AssertionError("bridge failure must surface as ProviderError")

for name in BLOCKED:
    assert name not in sys.modules, f"{name} leaked into sys.modules"
assert "pyodide" not in sys.modules
print(json.dumps({"ok": True, "version": info["version"], "pandas": info["pandas"]}))
'''


def test_bootstrap_and_dispatch_without_optional_dependencies():
    env = {**os.environ, "LOG_TO_CONSOLE": "false", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, "-c", _SCRIPT % {"blocked": json.dumps(BLOCKED)}], cwd=BACKEND_DIR,
                          capture_output=True, text=True, encoding="utf-8", env=env, timeout=180)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr[-4000:]}"
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["ok"] is True


def test_provider_modules_do_not_import_requests_or_pyodide_at_module_level():
    script = ("import sys\n"
              "import app.services.providers.tencent, app.services.providers.eastmoney, app.services.providers.sina_jsonp\n"
              "import app.services.providers.browser_market_data, app.browser.bootstrap, app.browser.testing\n"
              "assert 'pyodide' not in sys.modules\n"
              "import app.services.data_service as ds\n"
              "# 原生 akshare 会自行引入 requests；这里只断言 providers/browser 包本身不直接导入\n"
              "import inspect\n"
              "for m in (app.services.providers.tencent, app.services.providers.eastmoney, app.services.providers.sina_jsonp,\n"
              "          app.services.providers.browser_market_data, app.browser.bootstrap, app.browser.testing,\n"
              "          app.browser.dispatch, app.browser.app_factory):\n"
              "    src = inspect.getsource(m)\n"
              "    for line in src.splitlines():\n"
              "        s = line.strip()\n"
              "        assert not (s.startswith('import requests') and not line.startswith(' ')), m.__name__\n"
              "        assert 'import pyodide' not in s and 'from pyodide' not in s, m.__name__\n"
              "print('ok')\n")
    env = {**os.environ, "LOG_TO_CONSOLE": "false", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, "-c", script], cwd=BACKEND_DIR, capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=180)
    assert proc.returncode == 0, proc.stderr[-3000:]
    assert proc.stdout.strip().endswith("ok")
