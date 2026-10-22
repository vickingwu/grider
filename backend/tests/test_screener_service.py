"""screener_service._evaluate_one：历史行数过少（指标为 NaN）时应记为失败，响应 JSON 不得含 NaN。"""
import json
from unittest.mock import MagicMock

import pandas as pd

from app.services import screener_service


def _truncated_history_service(rows: int) -> MagicMock:
    """模拟数据源只返回 rows 根 K 线的 ETFAnalysisService；评估走真实 SuitabilityAnalyzer。"""
    from app.services.suitability_analyzer import SuitabilityAnalyzer

    dates = pd.date_range("2026-10-01", periods=rows, freq="B")
    df = pd.DataFrame({
        "date": dates, "open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0, "volume": 1000, "amount": 1000.0,
    })
    service = MagicMock()
    service.get_basic_info.return_value = {"name": "测试ETF", "current_price": 1.0, "date": "2026-10-01"}
    service.get_historical_data.return_value = df
    service.suitability_analyzer = SuitabilityAnalyzer()
    return service


def test_evaluate_one_reports_failed_on_non_finite_metrics(monkeypatch):
    monkeypatch.setattr(screener_service, "ETFAnalysisService", lambda country: _truncated_history_service(1))
    result = screener_service._evaluate_one({"code": "510300", "name": "沪深300ETF", "category": "宽基"})

    assert result["total_score"] is None
    assert result["error"] == screener_service._INSUFFICIENT_HISTORY_MESSAGE
    # 响应中不允许出现 NaN/Infinity（allow_nan=False 遇到即抛 ValueError）
    assert json.dumps({"results": [result]}, allow_nan=False, ensure_ascii=False)


def test_ensure_finite_passes_for_finite_values():
    screener_service._ensure_finite(1.0, 2.5, 0, 3)
