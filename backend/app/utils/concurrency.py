"""线程辅助：本地用真实线程/线程池；浏览器(Pyodide, sys.platform == "emscripten")无法创建线程，退化为串行。"""
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable, List, Optional, TypeVar

from app.utils.logger import get_logger

logger = get_logger(__name__)
T = TypeVar("T")
IS_PYODIDE = sys.platform == "emscripten"


def run_in_worker_thread(fn: Callable[[], T]) -> Optional[T]:
    """在独立守护线程中同步执行 fn 并返回结果；异常记录日志并返回 None（与原 holder.get() 语义一致）。
    Pyodide 下直接同步调用。"""
    holder: dict = {}

    def _target() -> None:
        try:
            holder["value"] = fn()
        except Exception as e:  # noqa: BLE001
            logger.error(f"后台任务执行失败: {e}", exc_info=True)

    if IS_PYODIDE:
        _target()
        return holder.get("value")
    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join()
    return holder.get("value")


def map_unordered(fn: Callable[[T], object], items: Iterable[T], max_workers: int) -> List:
    """并发映射，结果顺序不保证（调用方自行排序）。Pyodide 或 max_workers<=1 时串行。"""
    items = list(items)
    if IS_PYODIDE or max_workers <= 1:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(fn, item) for item in items]
        return [f.result() for f in as_completed(futures)]
