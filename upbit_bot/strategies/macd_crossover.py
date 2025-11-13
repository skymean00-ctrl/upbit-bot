"""MACD crossover strategy."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from .base import Candle, Strategy, StrategySignal


class MACDCrossoverStrategy(Strategy):
    """MACD crossover strategy using MACD and signal line."""

    name = "macd_crossover"

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
    ) -> None:
        if fast_period >= slow_period:
            raise ValueError("fast_period must be smaller than slow_period")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period

    def _ema(self, data: np.ndarray, period: int) -> np.ndarray:
        """Calculate Exponential Moving Average."""
        ema = np.zeros_like(data)
        ema[0] = data[0]
        multiplier = 2.0 / (period + 1)
        for i in range(1, len(data)):
            ema[i] = (data[i] * multiplier) + (ema[i - 1] * (1 - multiplier))
        return ema

    def on_candles(self, candles: Iterable[Candle]) -> StrategySignal:
        buffer: list[Candle] = list(candles)
        if len(buffer) < self.slow_period + self.signal_period:
            return StrategySignal.HOLD

        closes = np.array([c.close for c in buffer], dtype=float)

        # Calculate EMAs
        fast_ema = self._ema(closes, self.fast_period)
        slow_ema = self._ema(closes, self.slow_period)

        # Calculate MACD line
        macd_line = fast_ema - slow_ema

        # Calculate Signal line (EMA of MACD)
        signal_line = self._ema(macd_line, self.signal_period)

        # Get current and previous values
        current_macd = macd_line[-1]
        prev_macd = macd_line[-2]
        current_signal = signal_line[-1]
        prev_signal = signal_line[-2]

        # Golden cross: MACD crosses above signal
        if prev_macd <= prev_signal and current_macd > current_signal:
            return StrategySignal.BUY

        # Death cross: MACD crosses below signal
        if prev_macd >= prev_signal and current_macd < current_signal:
            return StrategySignal.SELL

        return StrategySignal.HOLD

