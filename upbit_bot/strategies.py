"""Core trading strategy definitions for the upbit bot."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from statistics import mean
from typing import Iterable, List, Sequence


@dataclass
class Candle:
    """Simple OHLC representation used by the backtesting engine."""

    timestamp: str
    open: float
    high: float
    low: float
    close: float


class TradingStrategy(ABC):
    """Abstract base class for deterministic trading strategies."""

    name: str = "abstract"

    @abstractmethod
    def generate_signal(
        self,
        index: int,
        candle: Candle,
        history: Sequence[Candle],
    ) -> int:
        """Return 1 for buy, -1 for sell, and 0 for hold."""


class MomentumStrategy(TradingStrategy):
    """Naive momentum strategy using short/long moving averages."""

    name = "momentum"

    def __init__(self, short_window: int = 3, long_window: int = 6) -> None:
        if short_window >= long_window:
            raise ValueError("short_window must be smaller than long_window")
        self.short_window = short_window
        self.long_window = long_window

    def _moving_average(self, candles: Sequence[Candle], window: int) -> float:
        prices = [candle.close for candle in candles[-window:]]
        return mean(prices)

    def generate_signal(
        self, index: int, candle: Candle, history: Sequence[Candle]
    ) -> int:
        if len(history) < self.long_window:
            return 0
        short_ma = self._moving_average(history, self.short_window)
        long_ma = self._moving_average(history, self.long_window)
        if short_ma > long_ma * 1.001:  # bias towards strong cross overs
            return 1
        if short_ma < long_ma * 0.999:
            return -1
        return 0


class MeanReversionStrategy(TradingStrategy):
    """Buy when price deviates significantly under the rolling mean."""

    name = "mean_reversion"

    def __init__(self, window: int = 5, threshold: float = 0.01) -> None:
        if window <= 1:
            raise ValueError("window must be larger than 1")
        self.window = window
        self.threshold = threshold

    def generate_signal(
        self, index: int, candle: Candle, history: Sequence[Candle]
    ) -> int:
        if len(history) < self.window:
            return 0
        prices = [candle.close for candle in history[-self.window :]]
        avg_price = mean(prices)
        deviation = (candle.close - avg_price) / avg_price
        if deviation <= -self.threshold:
            return 1
        if deviation >= self.threshold:
            return -1
        return 0


class BreakoutStrategy(TradingStrategy):
    """Buy breakouts over the recent high and sell on new lows."""

    name = "breakout"

    def __init__(self, lookback: int = 4) -> None:
        self.lookback = lookback

    def generate_signal(
        self, index: int, candle: Candle, history: Sequence[Candle]
    ) -> int:
        if len(history) < self.lookback:
            return 0
        window = history[-self.lookback :]
        highest = max(c.high for c in window)
        lowest = min(c.low for c in window)
        if candle.close > highest:
            return 1
        if candle.close < lowest:
            return -1
        return 0


def candles_from_rows(rows: Iterable[dict]) -> List[Candle]:
    """Helper to convert dictionary rows into :class:`Candle` objects."""

    candles: List[Candle] = []
    for row in rows:
        candles.append(
            Candle(
                timestamp=str(row["timestamp"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
            )
        )
    return candles


__all__ = [
    "Candle",
    "TradingStrategy",
    "MomentumStrategy",
    "MeanReversionStrategy",
    "BreakoutStrategy",
    "candles_from_rows",
]
