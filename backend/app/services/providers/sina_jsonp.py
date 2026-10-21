"""新浪 JSONP 数据源（仅在 worker 内通过 importScripts 加载；此处为 URL 构造与纯解析）。

- 日线 getKLineData：行 {day, open, high, low, close, volume}，不复权、成交量为“股”
- 外盘期货 getGlobalFuturesDailyKLine：行 {date, open, high, low, close, volume}
变量名按符号唯一（_k_{symbol} / _f_{sym}），避免 importScripts 的 var 全局互相覆盖。
"""
from typing import Any, Dict, List, Optional

import pandas as pd

# scale=240 日线；datalen=1970 约 8 年
SINA_KLINE_URL = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20{var}=/CN_MarketDataService.getKLineData"
                  "?symbol={symbol}&scale=240&ma=no&datalen=1970")
SINA_FUTURES_URL = ("https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20{var}=/"
                    "GlobalFuturesService.getGlobalFuturesDailyKLine?symbol={sym}")


def sina_var_name(symbol: str) -> str:
    """日线 JSONP 的全局变量名（按符号唯一）。"""
    return f"_k_{symbol}"


def futures_var_name(sym: str) -> str:
    """外盘期货 JSONP 的全局变量名（按符号唯一）。"""
    return f"_f_{sym}"


def _rows_to_frame(rows: Optional[List[Dict[str, Any]]], date_key: str) -> Optional[pd.DataFrame]:
    if not rows:
        return None
    records = [r for r in rows if isinstance(r, dict) and r.get(date_key)]
    if not records:
        return None
    df = pd.DataFrame(records).rename(columns={date_key: "date"})
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            df[col] = 0
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    df = df[["date", "open", "high", "low", "close", "volume"]]
    df["volume"] = df["volume"].fillna(0)
    return df.sort_values("date").reset_index(drop=True)


def parse_sina_kline(rows: Optional[List[Dict[str, Any]]]) -> Optional[pd.DataFrame]:
    """getKLineData 行列表 → DataFrame[date, open, high, low, close, volume, amount]（amount 按均价估算）。"""
    df = _rows_to_frame(rows, "day")
    if df is None:
        return None
    df["amount"] = ((df["open"] + df["close"] + df["high"] + df["low"]) / 4) * df["volume"]
    return df


def parse_sina_futures(rows: Optional[List[Dict[str, Any]]]) -> Optional[pd.DataFrame]:
    """getGlobalFuturesDailyKLine 行列表 → DataFrame[date, open, high, low, close, volume]。"""
    return _rows_to_frame(rows, "date")
