"""东方财富：push2his 日线解析、pingzhongdata 净值解析、JS 变量提取（纯函数，无网络）。"""
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from app.services.providers.errors import ProviderError

# push2his 日线：klt=101 日线；fqt 1=前复权(ETF/STOCK) 0=不复权(INDEX)
EM_KLINE_URL = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}&klt=101&fqt={fqt}"
                "&beg=20130101&end=20500101&fields1=f1,f2,f3,f4,f5,f6"
                "&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")
# 场外基金 pingzhongdata（含 fS_name / Data_netWorthTrend；URL 不带防缓存参数以保证 fixture 可复现）
PINGZHONG_URL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"
# 中国无夏令时，固定 +08:00 即可（无需 tzdata）
_CN_TZ = timezone(timedelta(hours=8))
# fields2 顺序：date,open,close,high,low,volume(手),amount,amplitude,pct,change,turnover
_EM_KLINE_FIELDS = ["date", "open", "close", "high", "low", "volume", "amount",
                    "amplitude", "change_pct", "change", "turnover"]


def secid_for(symbol: str) -> Optional[str]:
    """腾讯/新浪风格符号 → 东财 secid：sh→1.{code}，sz→0.{code}；bj 未验证 → None（跳过该跳）。"""
    sym = str(symbol).strip()
    prefix, code = sym[:2].lower(), sym[2:]
    if prefix == "sh":
        return f"1.{code}"
    if prefix == "sz":
        return f"0.{code}"
    return None


def parse_em_kline(text: str) -> pd.DataFrame:
    """解析 push2his 正文 → DataFrame[date, open, high, low, close, volume(手), amount, change_pct]。

    rc != 0 或 data 为 null → ProviderError。volume 保持“手”（与本地 em 兜底一致，不乘 100）。
    """
    try:
        payload = json.loads(text)
    except ValueError as e:
        raise ProviderError(f"东财日线正文不是 JSON: {e}") from e
    if not isinstance(payload, dict) or payload.get("rc") != 0 or not payload.get("data"):
        raise ProviderError("东财日线接口无数据(rc != 0 或 data 为空)")
    klines: List[str] = payload["data"].get("klines") or []
    records: List[Dict[str, Any]] = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 9:
            continue
        records.append(dict(zip(_EM_KLINE_FIELDS, parts)))
    if not records:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "amount", "change_pct"])
    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    for col in ["open", "high", "low", "close", "volume", "amount", "change_pct"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df[["date", "open", "high", "low", "close", "volume", "amount", "change_pct"]].reset_index(drop=True)


def em_epoch_ms_to_date(epoch_ms: float) -> str:
    """pingzhongdata 的毫秒时间戳 → 'YYYY-MM-DD'（北京时间，固定 +08:00）。"""
    return datetime.fromtimestamp(float(epoch_ms) / 1000.0, tz=_CN_TZ).date().isoformat()


def parse_networth_trend(rows: Optional[List[Dict[str, Any]]]) -> Optional[pd.DataFrame]:
    """Data_netWorthTrend [{x: epoch_ms, y: nav}, ...] → 日线字段（open=high=low=close=nav，volume=0，amount=0.0）。"""
    if not rows:
        return None
    dates: List[str] = []
    navs: List[Any] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("x") is None:
            continue
        dates.append(em_epoch_ms_to_date(row["x"]))
        navs.append(row.get("y"))
    if not dates:
        return None
    nav = pd.to_numeric(pd.Series(navs), errors="coerce")
    df = pd.DataFrame({
        "date": dates,
        "open": nav.values,
        "high": nav.values,
        "low": nav.values,
        "close": nav.values,
        "volume": 0,
    })
    df["amount"] = 0.0
    return df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)


def extract_js_vars(text: str, names: List[str]) -> Dict[str, Any]:
    """从 JS 脚本文本中提取 `var {name} = <JSON>` 的值（raw_decode 精确处理嵌套值）。

    允许值外层包一对括号 `(...)`；JSON null → None；任一名称缺失 → ProviderError
    （例如基金不存在时 301 → notfound.html 的 HTML 正文会在此干净地失败）。
    """
    if text is None:
        raise ProviderError("脚本正文为空")
    decoder = json.JSONDecoder()
    out: Dict[str, Any] = {}
    for name in names:
        match = re.search(r"var\s+" + re.escape(name) + r"\s*=", text)
        if match is None:
            raise ProviderError(f"脚本中缺少变量 {name}")
        idx = match.end()
        while idx < len(text) and text[idx] in " \t\r\n":
            idx += 1
        if idx < len(text) and text[idx] == "(":
            idx += 1
            while idx < len(text) and text[idx] in " \t\r\n":
                idx += 1
        try:
            value, _ = decoder.raw_decode(text, idx)
        except ValueError as e:
            raise ProviderError(f"变量 {name} 的值无法解析为 JSON: {e}") from e
        out[name] = value
    return out
