"""Lightweight backtesting utilities."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import pandas as pd

from upbit_bot.strategies import Candle, Strategy, StrategySignal


@dataclass
class TradeRecord:
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    pnl: float


@dataclass
class BacktestResult:
    trades: list[TradeRecord]
    total_return_pct: float
    win_rate: float
    max_drawdown_pct: float


class Backtester:
    """Simplified backtesting engine for single-market strategies."""

    def __init__(self, strategy: Strategy, fee_rate: float = 0.0005) -> None:
        self.strategy = strategy
        self.fee_rate = fee_rate

    def run(self, candles: Iterable[Candle]) -> BacktestResult:
        buffer = list(candles)
        if not buffer:
            return BacktestResult([], 0.0, 0.0, 0.0)

        position_price = None
        trades: list[TradeRecord] = []
        equity_curve: list[float] = [1.0]

        for idx in range(len(buffer)):
            signal = self.strategy.on_candles(buffer[: idx + 1])
            candle = buffer[idx]
            if signal is StrategySignal.BUY and position_price is None:
                position_price = candle.close * (1 + self.fee_rate)
            elif signal is StrategySignal.SELL and position_price is not None:
                exit_price = candle.close * (1 - self.fee_rate)
                pnl = (exit_price - position_price) / position_price
                trades.append(
                    TradeRecord(
                        entry_time=buffer[idx - 1].timestamp if idx > 0 else candle.timestamp,
                        entry_price=position_price,
                        exit_time=candle.timestamp,
                        exit_price=exit_price,
                        pnl=pnl,
                    )
                )
                equity_curve.append(equity_curve[-1] * (1 + pnl))
                position_price = None

        total_return = equity_curve[-1] - 1
        wins = sum(1 for t in trades if t.pnl > 0)
        win_rate = wins / len(trades) if trades else 0.0
        max_drawdown = self._max_drawdown(pd.Series(equity_curve))
        return BacktestResult(
            trades=trades,
            total_return_pct=total_return * 100,
            win_rate=win_rate * 100,
            max_drawdown_pct=max_drawdown * 100,
        )

    @staticmethod
    def _max_drawdown(series: pd.Series) -> float:
        if series.empty:
            return 0.0
        rolling_max = series.cummax()
        drawdowns = (series - rolling_max) / rolling_max
        return abs(drawdowns.min())
