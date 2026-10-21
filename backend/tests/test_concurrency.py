"""app.utils.concurrency 单元测试：串行(Pyodide)路径、线程路径、异常语义。"""
import threading

from app.utils import concurrency
from app.utils.concurrency import map_unordered, run_in_worker_thread


def test_run_in_worker_thread_runs_in_another_thread_natively():
    main_ident = threading.get_ident()
    seen = {}

    def _fn():
        seen["ident"] = threading.get_ident()
        return 42

    assert run_in_worker_thread(_fn) == 42
    assert seen["ident"] != main_ident


def test_run_in_worker_thread_serial_when_pyodide(monkeypatch):
    monkeypatch.setattr(concurrency, "IS_PYODIDE", True)
    main_ident = threading.get_ident()
    seen = {}

    def _fn():
        seen["ident"] = threading.get_ident()
        return "ok"

    assert run_in_worker_thread(_fn) == "ok"
    assert seen["ident"] == main_ident


def test_run_in_worker_thread_exception_returns_none_and_logs(caplog):
    def _boom():
        raise RuntimeError("boom")

    with caplog.at_level("ERROR", logger="app.utils.concurrency"):
        assert run_in_worker_thread(_boom) is None
    assert any("后台任务执行失败" in rec.getMessage() for rec in caplog.records)


def test_run_in_worker_thread_exception_serial_returns_none(monkeypatch):
    monkeypatch.setattr(concurrency, "IS_PYODIDE", True)

    def _boom():
        raise ValueError("x")

    assert run_in_worker_thread(_boom) is None


def test_map_unordered_thread_path_returns_all_results():
    out = map_unordered(lambda x: x * 2, [1, 2, 3, 4, 5], max_workers=3)
    assert sorted(out) == [2, 4, 6, 8, 10]


def test_map_unordered_serial_when_pyodide_preserves_order(monkeypatch):
    monkeypatch.setattr(concurrency, "IS_PYODIDE", True)
    idents = []

    def _fn(x):
        idents.append(threading.get_ident())
        return x + 1

    out = map_unordered(_fn, [1, 2, 3], max_workers=4)
    assert out == [2, 3, 4]
    assert set(idents) == {threading.get_ident()}


def test_map_unordered_serial_when_single_worker():
    out = map_unordered(lambda x: x, iter(["a", "b"]), max_workers=1)
    assert out == ["a", "b"]
