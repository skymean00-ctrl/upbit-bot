"""Lightweight backtesting utilities."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from upbit_bot.strategies import Candle, StrategySignal

try:
    from upbit_bot.strategies import BaseStrategy
except ImportError:
    BaseStrategy = None  # type: ignore


# Strategy Protocol for backward compatibility
class Strategy(Protocol):
    """Protocol for strategy interface."""

    def on_candles(self, candles: Iterable[Candle]) -> StrategySignal:
        """Generate signal from candles."""
        ...


@dataclass
class TradeRecord:
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    pnl: float
    pnl_pct: float


@dataclass
class BacktestResult:
    trades: list[TradeRecord]
    total_return_pct: float
    win_rate: float
    max_drawdown_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win_pct: float
    avg_loss_pct: float
    sharpe_ratio: float
    initial_balance: float
    final_balance: float

    def model_dump(self) -> dict:
        """Pydantic compatibility."""
        return {
            "trades": [
                {
                    "entry_time": t.entry_time,
                    "entry_price": t.entry_price,
                    "exit_time": t.exit_time,
                    "exit_price": t.exit_price,
                    "pnl": t.pnl,
                    "pnl_pct": t.pnl_pct,
                }
                for t in self.trades
            ],
            "total_return_pct": self.total_return_pct,
            "win_rate": self.win_rate,
            "max_drawdown_pct": self.max_drawdown_pct,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "avg_win_pct": self.avg_win_pct,
            "avg_loss_pct": self.avg_loss_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "initial_balance": self.initial_balance,
            "final_balance": self.final_balance,
        }


class Backtester:
    """Simplified backtesting engine for single-market strategies."""

    def __init__(
        self,
        strategy: Strategy | BaseStrategy,  # type: ignore
        initial_balance: float = 1000000.0,
        fee_rate: float = 0.0005,
        slippage_pct: float = 0.001,
    ) -> None:
        self.strategy = strategy
        self.initial_balance = initial_balance
        self.fee_rate = fee_rate
        self.slippage_pct = slippage_pct

    def _get_signal(self, candles: list[Candle]) -> StrategySignal:
        """Get signal from strategy, supporting both old and new interfaces."""
        # Try new BaseStrategy interface first
        if hasattr(self.strategy, "determine_signal"):
            return self.strategy.determine_signal(candles)  # type: ignore
        # Try get_signal method
        if hasattr(self.strategy, "get_signal"):
            result = self.strategy.get_signal(candles)  # type: ignore
            if result is None:
                return StrategySignal.HOLD
            return result
        # Fall back to on_candles
        return self.strategy.on_candles(candles)  # type: ignore

    def run(self, candles: Iterable[Candle]) -> BacktestResult:
        buffer = list(candles)
        if not buffer:
            return BacktestResult(
                trades=[],
                total_return_pct=0.0,
                win_rate=0.0,
                max_drawdown_pct=0.0,
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                avg_win_pct=0.0,
                avg_loss_pct=0.0,
                sharpe_ratio=0.0,
                initial_balance=self.initial_balance,
                final_balance=self.initial_balance,
            )

        position_price = None
        trades: list[TradeRecord] = []
        balance = self.initial_balance
        equity_curve: list[float] = [balance]

        for idx in range(len(buffer)):
            signal = self._get_signal(buffer[: idx + 1])
            candle = buffer[idx]

            if signal is StrategySignal.BUY and position_price is None:
                # 매수: 수수료 + 슬리피지 반영
                entry_price = candle.close * (1 + self.fee_rate + self.slippage_pct)
                position_price = entry_price

            elif signal is StrategySignal.SELL and position_price is not None:
                # 매도: 수수료 + 슬리피지 반영
                exit_price = candle.close * (1 - self.fee_rate - self.slippage_pct)
                pnl_pct = (exit_price - position_price) / position_price
                pnl = balance * pnl_pct
                balance += pnl

                trades.append(
                    TradeRecord(
                        entry_time=buffer[idx - 1].timestamp if idx > 0 else candle.timestamp,
                        entry_price=position_price,
                        exit_time=candle.timestamp,
                        exit_price=exit_price,
                        pnl=pnl,
                        pnl_pct=pnl_pct * 100,
                    )
                )
                equity_curve.append(balance)
                position_price = None

        # 통계 계산
        total_return = ((balance - self.initial_balance) / self.initial_balance) * 100
        winning_trades = [t for t in trades if t.pnl > 0]
        losing_trades = [t for t in trades if t.pnl <= 0]

        win_rate = (len(winning_trades) / len(trades) * 100) if trades else 0.0
        avg_win_pct = (
            sum(t.pnl_pct for t in winning_trades) / len(winning_trades) if winning_trades else 0.0
        )
        avg_loss_pct = (
            sum(t.pnl_pct for t in losing_trades) / len(losing_trades) if losing_trades else 0.0
        )

        max_drawdown = self._max_drawdown(pd.Series(equity_curve)) * 100

        # Sharpe Ratio 계산 (연율화, 무위험 수익률 0 가정)
        if trades:
            returns = pd.Series([t.pnl_pct for t in trades])
            sharpe_ratio = (
                (returns.mean() / returns.std() * (252**0.5)) if returns.std() > 0 else 0.0
            )
        else:
            sharpe_ratio = 0.0

        return BacktestResult(
            trades=trades,
            total_return_pct=total_return,
            win_rate=win_rate,
            max_drawdown_pct=max_drawdown,
            total_trades=len(trades),
            winning_trades=len(winning_trades),
            losing_trades=len(losing_trades),
            avg_win_pct=avg_win_pct,
            avg_loss_pct=avg_loss_pct,
            sharpe_ratio=sharpe_ratio,
            initial_balance=self.initial_balance,
            final_balance=balance,
        )

    @staticmethod
    def _max_drawdown(series: pd.Series) -> float:
        if series.empty:
            return 0.0
        rolling_max = series.cummax()
        drawdowns = (series - rolling_max) / rolling_max
        return abs(drawdowns.min())
