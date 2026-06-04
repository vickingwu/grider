"""
均线策略回测引擎

不同于网格策略（震荡套利），均线策略是趋势跟随：
- 价格上穿均线（金叉） → 按设定仓位比例买入
- 价格下穿均线（死叉） → 清仓卖出

支持单均线（价格 vs 一条均线）模式，均线类型 SMA / EMA，周期可配。
输出结构与网格回测引擎完全一致（trade_records / equity_curve / final_state），
以便复用现有指标计算器与前端图表。
"""

from typing import List, Dict, Optional
import pandas as pd

from .models import KBar, TradeRecord, BacktestState
from .fee_calculator import FeeCalculator


def calculate_ma(closes: List[float], period: int, ma_type: str = "SMA") -> List[Optional[float]]:
    """计算移动平均线，返回与 closes 等长的列表（前 period-1 个为 None）。"""
    if period <= 0:
        period = 1
    s = pd.Series(closes, dtype="float64")
    if ma_type.upper() == "EMA":
        # 至少有 period 个数据后才认为均线有效，前 period-1 个置 None
        ma = s.ewm(span=period, adjust=False).mean()
        result = ma.tolist()
        for i in range(min(period - 1, len(result))):
            result[i] = None
        return result
    else:  # SMA
        ma = s.rolling(window=period, min_periods=period).mean()
        return [None if pd.isna(v) else float(v) for v in ma.tolist()]


class MABacktestEngine:
    """均线策略回测引擎"""

    def __init__(self, ma_params: dict, fee_calculator: FeeCalculator,
                 total_capital: float, country: str = "CHN"):
        """
        Args:
            ma_params: {
                'period': int,           # 均线周期
                'ma_type': 'SMA'|'EMA',  # 均线类型
                'position_ratio': float, # 买入仓位比例 0-1（默认1.0满仓）
            }
            fee_calculator: 手续费计算器
            total_capital: 总资金
            country: 市场（决定最小交易单位）
        """
        self.period = int(ma_params.get("period", 20))
        self.ma_type = ma_params.get("ma_type", "SMA")
        self.position_ratio = float(ma_params.get("position_ratio", 1.0))
        if self.position_ratio <= 0 or self.position_ratio > 1:
            self.position_ratio = 1.0
        self.fee_calc = fee_calculator
        self.total_capital = total_capital
        self.country = country
        # 最小交易单位：A股/港股按手(100股)，美股 1 股
        self.min_unit = 1 if country == "USA" else 100

        self.trade_records: List[TradeRecord] = []
        self.equity_curve: List[Dict] = []
        self.ma_series: List[Optional[float]] = []

    def _round_quantity(self, quantity: int) -> int:
        """按最小交易单位向下取整。"""
        return (quantity // self.min_unit) * self.min_unit

    def run(self, kline_data: List[KBar]) -> Dict:
        if not kline_data:
            raise ValueError("K线数据为空")

        closes = [k.close for k in kline_data]
        self.ma_series = calculate_ma(closes, self.period, self.ma_type)

        cash = self.total_capital
        position = 0
        prev_above: Optional[bool] = None  # 上一根：价格是否在均线之上

        for i, kbar in enumerate(kline_data):
            ma = self.ma_series[i]
            price = kbar.close

            # 均线尚未生效（数据不足）：仅记录资产曲线
            if ma is None:
                total_asset = cash + position * price
                self.equity_curve.append({"time": kbar.time, "total_asset": total_asset, "price": price})
                continue

            above = price > ma

            # 信号判定（穿越）
            if prev_above is not None:
                # 金叉：由下方穿到上方 → 买入
                if above and not prev_above and position == 0 and cash > 0:
                    invest = cash * self.position_ratio
                    raw_qty = int(invest / price) if price > 0 else 0
                    qty = self._round_quantity(raw_qty)
                    if qty > 0:
                        cost = self.fee_calc.calculate_buy_cost(price, qty)
                        # 资金不足则减一手
                        while cost > cash and qty > self.min_unit:
                            qty -= self.min_unit
                            cost = self.fee_calc.calculate_buy_cost(price, qty)
                        if qty > 0 and cost <= cash:
                            commission = self.fee_calc.calculate(price * qty)
                            cash -= cost
                            position += qty
                            self.trade_records.append(TradeRecord(
                                time=kbar.time, type="BUY", price=price, quantity=qty,
                                commission=commission, profit=None, position=position, cash=cash,
                            ))
                # 死叉：由上方穿到下方 → 清仓
                elif not above and prev_above and position > 0:
                    income = self.fee_calc.calculate_sell_income(price, position)
                    commission = self.fee_calc.calculate(price * position)
                    cash += income
                    sold_qty = position
                    position = 0
                    self.trade_records.append(TradeRecord(
                        time=kbar.time, type="SELL", price=price, quantity=sold_qty,
                        commission=commission, profit=None, position=position, cash=cash,
                    ))

            prev_above = above

            total_asset = cash + position * price
            self.equity_curve.append({"time": kbar.time, "total_asset": total_asset, "price": price})

        final_asset = cash + position * kline_data[-1].close
        return {
            "trade_records": self.trade_records,
            "equity_curve": self.equity_curve,
            "final_state": {"cash": cash, "position": position, "total_asset": final_asset},
            "kline_data": kline_data,
            "ma_series": self.ma_series,
        }
