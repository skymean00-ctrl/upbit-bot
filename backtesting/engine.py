"""Backtesting engine that simulates strategies on historical data."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

from upbit_bot.strategies import Candle, TradingStrategy, candles_from_rows


@dataclass
class BacktestResult:
    """Summary data class returned by :class:`BacktestingEngine`."""

    total_return: float
    win_rate: float
    trades: int
    equity_curve: List[float]


class BacktestingEngine:
    """Simple engine supporting single-position backtests."""

    def __init__(
        self,
        strategy: TradingStrategy,
        initial_capital: float = 1_000_000,
        fee_rate: float = 0.0005,
    ) -> None:
        self.strategy = strategy
        self.initial_capital = float(initial_capital)
        self.fee_rate = fee_rate

    def _run_internal(self, candles: Sequence[Candle]) -> BacktestResult:
        cash = self.initial_capital
        position = 0  # number of coins (max 1)
        entry_price = 0.0
        wins = 0
        trade_returns: List[float] = []
        equity_curve: List[float] = []

        for index, candle in enumerate(candles):
            signal = self.strategy.generate_signal(index, candle, candles[: index + 1])
            price = candle.close
            if signal > 0 and position == 0:
                position = 1
                entry_price = price
                cash -= price * (1 + self.fee_rate)
            elif signal < 0 and position == 1:
                position = 0
                exit_price = price
                cash += exit_price * (1 - self.fee_rate)
                trade_return = (exit_price - entry_price) / entry_price
                trade_returns.append(trade_return)
                if trade_return > 0:
                    wins += 1
            equity_curve.append(cash + position * price)

        # Close open position at last candle price
        if position == 1:
            final_price = candles[-1].close
            cash += final_price * (1 - self.fee_rate)
            trade_return = (final_price - entry_price) / entry_price
            trade_returns.append(trade_return)
            if trade_return > 0:
                wins += 1
            equity_curve[-1] = cash

        trades = len(trade_returns)
        total_return = (cash - self.initial_capital) / self.initial_capital
        win_rate = wins / trades if trades else 0.0
        return BacktestResult(
            total_return=total_return,
            win_rate=win_rate,
            trades=trades,
            equity_curve=equity_curve,
        )

    def run(self, rows: Iterable[dict] | Sequence[Candle]) -> BacktestResult:
        """Execute the backtest using iterable dictionaries or Candle objects."""

        if isinstance(rows, Sequence) and rows and isinstance(rows[0], Candle):
            candles = list(rows)  # type: ignore[assignment]
        else:
            candles = candles_from_rows(rows)  # type: ignore[arg-type]
        if not candles:
            raise ValueError("No market data supplied to the backtesting engine.")
        return self._run_internal(candles)


__all__ = ["BacktestingEngine", "BacktestResult"]
