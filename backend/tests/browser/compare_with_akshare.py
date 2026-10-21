"""浏览器数据层 vs AkShare 数据一致性校验（design §11.2 预注册规则；需要网络，原生 requests 钩子）。

用法（在 backend 目录）：python -m tests.browser.compare_with_akshare --out ..\\..\\.agents\\evidence\\parity.md
输出 Markdown PASS/FAIL 表；任一 FAIL → 退出码 1。所有比较均排除当日（盘中规则单独测试）。
"""
import argparse
import os
import sys
import traceback
from datetime import date, datetime, timedelta
from typing import Callable, List, Optional, Tuple

import numpy as np
import pandas as pd

from app.services.data_service import DataService
from app.services.providers.browser_market_data import BrowserMarketData, drop_live_bar

Row = Tuple[str, str, str, str]  # (series, rule, PASS/FAIL/SKIP, detail)
_WINDOW_DAYS = 200
_SESSIONS = 120
_OHLC = ["open", "high", "low", "close"]
EM_UNREACHABLE = "em 不可达，跳过"


def _today() -> str:
    return date.today().isoformat()


def _completed(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame(columns=["date"])
    return df[df["date"] < _today()].reset_index(drop=True)


def _aligned(native: pd.DataFrame, browser: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, bool]:
    """取 AkShare 最后 _SESSIONS 个已完成交易日，浏览器侧对齐到同一日期集合。"""
    nat = _completed(native).tail(_SESSIONS).reset_index(drop=True)
    dates = set(nat["date"])
    brw = _completed(browser)
    brw = brw[brw["date"].isin(dates)].reset_index(drop=True)
    same_dates = nat["date"].tolist() == brw["date"].tolist() and len(nat) == _SESSIONS
    return nat, brw, same_dates


def _median_ratio(a: pd.Series, b: pd.Series) -> float:
    ratio = (a.astype(float) / b.astype(float)).replace([np.inf, -np.inf], np.nan).dropna()
    return float(ratio.median()) if len(ratio) else float("nan")


def _within_two_pct(ratio: float) -> bool:
    return abs(ratio - 1.0) <= 0.02


def check_etf(native: DataService, browser: BrowserMarketData) -> Row:
    start = (date.today() - timedelta(days=_WINDOW_DAYS)).isoformat()
    nat, brw, same = _aligned(native.get_daily_data("510300", "XSHG", "ETF", start, _today()),
                              browser.get_daily_data("510300", "XSHG", "ETF", start, _today()))
    if not same:
        return ("510300 ETF", "dates identical, |ΔOHLC|≤0.001, volume ratio ±2%", "FAIL",
                f"dates differ: native {len(nat)} rows, browser {len(brw)} rows")
    max_delta = max(float((nat[c].astype(float) - brw[c].astype(float)).abs().max()) for c in _OHLC)
    ratio = _median_ratio(brw["volume"], nat["volume"])
    ok = max_delta <= 0.001 and _within_two_pct(ratio)
    return ("510300 ETF", "dates identical, |ΔOHLC|≤0.001, volume ratio ±2%", "PASS" if ok else "FAIL",
            f"max|Δ|={max_delta:.4f}, median volume ratio={ratio:.4f}")


def check_stock(native: DataService, browser: BrowserMarketData) -> Row:
    start = (date.today() - timedelta(days=_WINDOW_DAYS)).isoformat()
    nat, brw, same = _aligned(native.get_daily_data("600519", "XSHG", "STOCK", start, _today()),
                              browser.get_daily_data("600519", "XSHG", "STOCK", start, _today()))
    rule = "dates identical and (|Δclose|≤0.001 or constant factor w/ return corr≥0.9999); volume ratio ±2%"
    if not same:
        return ("600519 STOCK qfq", rule, "FAIL", f"dates differ: native {len(nat)}, browser {len(brw)}")
    max_delta = float((nat["close"].astype(float) - brw["close"].astype(float)).abs().max())
    factor = _median_ratio(brw["close"], nat["close"])
    corr = float(np.corrcoef(nat["close"].astype(float).pct_change().dropna(),
                             brw["close"].astype(float).pct_change().dropna())[0, 1])
    vol_ratio = _median_ratio(brw["volume"], nat["volume"])
    price_ok = max_delta <= 0.001 or corr >= 0.9999
    ok = price_ok and _within_two_pct(vol_ratio)
    return ("600519 STOCK qfq", rule, "PASS" if ok else "FAIL",
            f"max|Δclose|={max_delta:.4f}, level factor={factor:.6f}, return corr={corr:.6f}, volume ratio={vol_ratio:.4f}")


def check_index(native: DataService, browser: BrowserMarketData) -> Row:
    start = (date.today() - timedelta(days=_WINDOW_DAYS)).isoformat()
    nat, brw, same = _aligned(native.get_daily_data("000300", "XSHG", "INDEX", start, _today()),
                              browser.get_daily_data("000300", "XSHG", "INDEX", start, _today()))
    rule = "dates identical, |Δclose|≤0.01, volume ratio ±2% (confirms ×100 for INDEX)"
    if not same:
        return ("000300 INDEX", rule, "FAIL", f"dates differ: native {len(nat)}, browser {len(brw)}")
    max_delta = float((nat["close"].astype(float) - brw["close"].astype(float)).abs().max())
    ratio = _median_ratio(brw["volume"], nat["volume"])
    ok = max_delta <= 0.01 and _within_two_pct(ratio)
    return ("000300 INDEX", rule, "PASS" if ok else "FAIL",
            f"max|Δclose|={max_delta:.4f}, median volume ratio (browser/native)={ratio:.4f}; "
            f"native unit sample volume={float(nat['volume'].iloc[-1]):.0f}")


def check_fund(native: DataService, browser: BrowserMarketData) -> Row:
    nat = _completed(native.get_daily_data("110011", "", "FUND", "2000-01-01", _today())).tail(_SESSIONS)
    brw = _completed(browser.get_daily_data("110011", "", "FUND", "2000-01-01", _today()))
    brw = brw[brw["date"].isin(set(nat["date"]))].reset_index(drop=True)
    nat = nat.reset_index(drop=True)
    rule = "dates identical, NAV identical to 4 dp"
    if nat["date"].tolist() != brw["date"].tolist() or len(nat) == 0:
        return ("110011 FUND NAV", rule, "FAIL", f"dates differ: native {len(nat)}, browser {len(brw)}")
    delta = float((nat["close"].astype(float).round(4) - brw["close"].astype(float).round(4)).abs().max())
    return ("110011 FUND NAV", rule, "PASS" if delta == 0.0 else "FAIL", f"max|ΔNAV(4dp)|={delta:.4f}, rows={len(nat)}")


def _em_minute(period: str) -> Optional[pd.DataFrame]:
    try:
        import akshare as ak
        start_dt = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d 09:30:00")
        end_dt = datetime.now().strftime("%Y-%m-%d 15:00:00")
        return ak.fund_etf_hist_min_em(symbol="510300", period=period, start_date=start_dt, end_date=end_dt, adjust="")
    except Exception:  # noqa: BLE001  本机 IP 被 push2his 封禁时的预期结果
        return None


def check_60min(browser: BrowserMarketData) -> Row:
    rule = "if em reachable: overlapping time identical and |Δclose|≤0.001 (Tencent side after drop-live-bar)"
    em = _em_minute("60")
    if em is None or len(em) == 0:
        return ("60-minute series", rule, "SKIP", EM_UNREACHABLE)
    try:
        tx = browser.get_intraday_kline("510300", "XSHG", "m60")
    except Exception as e:  # noqa: BLE001
        return ("60-minute series", rule, "FAIL", f"Tencent mkline failed: {e}")
    tx_df = pd.DataFrame({"time": [k.time.strftime("%Y-%m-%d %H:%M") for k in tx], "close": [k.close for k in tx],
                          "date": [k.time.strftime("%Y-%m-%d") for k in tx]})
    tx_df = drop_live_bar(tx_df)
    em = em.rename(columns={"时间": "time", "收盘": "close"})
    em["time"] = pd.to_datetime(em["time"]).dt.strftime("%Y-%m-%d %H:%M")
    merged = em.merge(tx_df, on="time", suffixes=("_em", "_tx"))
    if len(merged) == 0:
        return ("60-minute series", rule, "FAIL", "no overlapping bars")
    delta = float((merged["close_em"].astype(float) - merged["close_tx"].astype(float)).abs().max())
    return ("60-minute series", rule, "PASS" if delta <= 0.001 else "FAIL", f"overlap={len(merged)}, max|Δclose|={delta:.4f}")


def check_5min(native: DataService, browser: BrowserMarketData) -> Row:
    rule = "identical when both daily; if native returns 5-minute bars, documented 口径 difference (not FAIL)"
    start = (date.today() - timedelta(days=20)).isoformat()
    nat = native.get_5min_kline("510300", "XSHG", start, _today(), "ETF")
    brw = browser.get_5min_kline("510300", "XSHG", start, _today(), "ETF")
    nat_daily = all(k.time.hour == 15 and k.time.minute == 0 for k in nat) if nat else True
    if not nat_daily:
        return ("get_5min_kline", rule, "PASS", f"native returned {len(nat)} 5-minute bars (em reachable); browser daily {len(brw)} — 口径 difference documented")
    nat_c = [(k.time.strftime("%Y-%m-%d"), round(k.close, 4)) for k in nat if k.time.strftime("%Y-%m-%d") < _today()]
    brw_c = [(k.time.strftime("%Y-%m-%d"), round(k.close, 4)) for k in brw if k.time.strftime("%Y-%m-%d") < _today()]
    ok = nat_c == brw_c
    return ("get_5min_kline", rule, "PASS" if ok else "FAIL", f"both daily; native {len(nat_c)} vs browser {len(brw_c)} bars, identical={ok}")


def _run(name: str, fn: Callable[[], Row]) -> Row:
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return (name, "-", "FAIL", f"exception: {type(e).__name__}: {e}")


def render(rows: List[Row]) -> str:
    lines = [f"# 浏览器数据层 vs AkShare 一致性（{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}）", "",
             "| series | rule | result | detail |", "|---|---|---|---|"]
    for series, rule, result, detail in rows:
        lines.append(f"| {series} | {rule} | **{result}** | {detail} |")
    lines.append("")
    lines.append(f"FAIL rows: {sum(1 for r in rows if r[2] == 'FAIL')}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="浏览器数据层 vs AkShare 一致性校验")
    parser.add_argument("--out", default=None, help="Markdown 输出路径")
    args = parser.parse_args(argv)

    native = DataService()
    browser = BrowserMarketData()
    rows: List[Row] = [
        _run("510300 ETF", lambda: check_etf(native, browser)),
        _run("600519 STOCK qfq", lambda: check_stock(native, browser)),
        _run("000300 INDEX", lambda: check_index(native, browser)),
        _run("110011 FUND NAV", lambda: check_fund(native, browser)),
        _run("60-minute series", lambda: check_60min(browser)),
        _run("get_5min_kline", lambda: check_5min(native, browser)),
    ]
    md = render(rows)
    print(md)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(md)
    return 1 if any(r[2] == "FAIL" for r in rows) else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
