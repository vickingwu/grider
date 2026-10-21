"""浏览器(Pyodide)在线引擎：应用工厂、分发器、启动引导、fixture 钩子。

所有模块均可在本机原生导入（单元测试 / CLI），不依赖 pyodide。
"""
from app.browser.app_factory import create_browser_app
from app.browser.dispatch import dispatch, dispatch_json

__all__ = ["create_browser_app", "dispatch", "dispatch_json"]
