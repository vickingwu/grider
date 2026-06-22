"""鱼盆模型服务（市场风向标）

对一组主流宽基指数，以「20日均线」为临界值（可选上下浮动 buffer），
判断当前收盘价相对临界值的强弱（YES/NO），并衍生偏离率、趋势强度、状态持续时间。

定位：判断市场大势/风格强弱，非买卖信号。

复用：data_service 取指数日线、ma_engine.calculate_ma 算20日线。串行执行 + 结果缓存。
"""

import time
import threading
from datetime import datetime, timedelta
from typing import Dict, List

from app.services.data_service import _cache_get, _cache_set, DataService
from app.algorithms.backtest.ma_engine import calculate_ma
from app.utils.helper import determine_country
from app.services.etf_analysis_service import ETFAnalysisService
from app.utils.logger import get_logger

logger = get_logger(__name__)

_CACHE_TTL = 1800  # 30 分钟（盘后数据基本不变）
_MA_PERIOD = 20

# 鱼盆模型默认指数清单（宽基，对齐博主表格 + 北证50；微盘股免费源无数据，剔除）
FISH_BASIN_INDICES: List[Dict] = [
    {"code": "000016", "name": "上证50"},
    {"code": "000300", "name": "沪深300"},
    {"code": "000688", "name": "科创50"},
    {"code": "399006", "name": "创业板指"},
    {"code": "000905", "name": "中证500"},
    {"code": "000852", "name": "中证1000"},
    {"code": "932000", "name": "中证2000"},
    {"code": "899050", "name": "北证50"},
]


def _evaluate_one(item: Dict, buffer_pct: float) -> Dict:
    """对单个指数计算鱼盆模型字段。buffer_pct 为浮动百分比（如 2 表示 ±2%）。"""
    code = str(item.get("code", "")).strip()
    name = item.get("name", code)
    try:
        processed_code, country = determine_country(code)
        svc = ETFAnalysisService(country=country)
        info = svc.data_client.search_by_ticker(processed_code, country)
        exchange_code = info.get("exchange_code", "")
        sec_type = info.get("type", "INDEX")
        disp_name = info.get("name") or name

        # 取最近约 90 个自然日的日线（>120天会走日线，这里用 get_5min_kline 的日线兜底路径）
        ds: DataService = svc.data_client
        today = datetime.now()
        end_date = today.strftime("%Y-%m-%d")
        # 取足够长的窗口以保证 20 日线 + 找状态翻转（约 250 个交易日）
        start_date = (today - timedelta(days=400)).strftime("%Y-%m-%d")
        kbars = ds.get_5min_kline(processed_code, exchange_code, start_date, end_date, sec_type)
        if not kbars or len(kbars) < _MA_PERIOD + 1:
            raise ValueError("数据不足以计算20日线")

        kbars = sorted(kbars, key=lambda k: k.time)
        closes = [float(k.close) for k in kbars]
        dates = [k.time.strftime("%Y-%m-%d") for k in kbars]

        ma = calculate_ma(closes, _MA_PERIOD, "SMA")
        factor = 1.0 + (buffer_pct or 0) / 100.0

        # 当前值
        cur_close = closes[-1]
        cur_ma = ma[-1]
        if cur_ma is None:
            raise ValueError("20日线无效")
        cur_threshold = cur_ma * factor
        cur_status = "YES" if cur_close >= cur_threshold else "NO"

        # 涨幅%（最新一日）
        change_pct = None
        if len(closes) >= 2 and closes[-2]:
            change_pct = (cur_close / closes[-2] - 1)

        # 偏离率
        deviation = (cur_close - cur_threshold) / cur_threshold if cur_threshold else None

        # 状态持续时间：从最新往回找，最近一次状态翻转的日期
        status_since = dates[-1]
        for i in range(len(closes) - 2, -1, -1):
            if ma[i] is None:
                break
            th = ma[i] * factor
            st = "YES" if closes[i] >= th else "NO"
            if st != cur_status:
                # i+1 是翻转后的第一天
                status_since = dates[i + 1]
                break
            status_since = dates[i]

        return {
            "code": code,
            "name": disp_name,
            "change_pct": round(change_pct, 4) if change_pct is not None else None,
            "price": round(cur_close, 3),
            "threshold": round(cur_threshold, 3),
            "ma20": round(cur_ma, 3),
            "status": cur_status,
            "status_since": status_since,
            "deviation": round(deviation, 4) if deviation is not None else None,
            "latest_date": dates[-1],
            "error": None,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"鱼盆模型评估失败 {code}: {e}")
        return {
            "code": code, "name": name, "status": None,
            "deviation": None, "error": str(e),
        }


def _assign_strength(results: List[Dict]) -> None:
    """按偏离率绝对值降序赋趋势强度（1=最强）。失败项不参与排名。"""
    valid = [r for r in results if r.get("deviation") is not None]
    valid.sort(key=lambda r: abs(r["deviation"]), reverse=True)
    for rank, r in enumerate(valid, start=1):
        r["strength"] = rank
    for r in results:
        if r.get("deviation") is None:
            r["strength"] = None


def _market_state(results: List[Dict]) -> Dict:
    valid = [r for r in results if r.get("status") in ("YES", "NO")]
    total = len(valid)
    yes = sum(1 for r in valid if r["status"] == "YES")
    if total == 0:
        label, tone = "未知", "neutral"
    elif yes == total:
        label, tone = "牛市状态", "bull"
    elif yes == 0:
        label, tone = "熊市状态", "bear"
    else:
        label, tone = "分化/震荡", "mixed"
    return {"label": label, "tone": tone, "yes": yes, "total": total}


class FishBasinService:
    """鱼盆模型批量评估（同步 + 缓存）"""

    def evaluate(self, indices: List[Dict] = None, buffer_pct: float = 0.0,
                 force_refresh: bool = False) -> Dict:
        indices = indices or FISH_BASIN_INDICES
        cache_key = f"fish_basin:{_MA_PERIOD}:{buffer_pct}:{len(indices)}"
        if not force_refresh:
            cached = _cache_get(cache_key)
            if cached is not None:
                out = dict(cached)
                out["from_cache"] = True
                return out

        holder = {}

        def _worker():
            holder["payload"] = self._do_eval(indices, cache_key, buffer_pct)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        t.join()
        return holder.get("payload") or {
            "results": [], "market_state": _market_state([]),
            "buffer_pct": buffer_pct, "ma_period": _MA_PERIOD,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "from_cache": False,
        }

    def _do_eval(self, indices: List[Dict], cache_key: str, buffer_pct: float) -> Dict:
        start = time.time()
        results: List[Dict] = []
        for item in indices:  # 串行（V8 非线程安全）
            results.append(_evaluate_one(item, buffer_pct))

        _assign_strength(results)
        # 默认按趋势强度升序（1 在前），失败项置后
        results.sort(key=lambda r: (r.get("strength") is None, r.get("strength") or 9999))

        payload = {
            "results": results,
            "market_state": _market_state(results),
            "buffer_pct": buffer_pct,
            "ma_period": _MA_PERIOD,
            "elapsed_seconds": round(time.time() - start, 1),
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "from_cache": False,
        }
        _cache_set(cache_key, payload, ttl=_CACHE_TTL)
        ms = payload["market_state"]
        logger.info(f"鱼盆模型完成: {ms['yes']}/{ms['total']} YES, 耗时{payload['elapsed_seconds']}s")
        return payload
