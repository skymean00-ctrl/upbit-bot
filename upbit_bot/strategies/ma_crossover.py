"""Simple moving-average crossover strategy with optional ATR filter."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from .base import Candle, Strategy, StrategySignal


class MovingAverageCrossoverStrategy(Strategy):
    name = "ma_crossover"

    def __init__(
        self,
        short_window: int = 14,
        long_window: int = 37,
        atr_window: int = 14,
        atr_threshold: float = 0.0,
    ) -> None:
        if short_window >= long_window:
            raise ValueError("short_window must be smaller than long_window")
        self.short_window = short_window
        self.long_window = long_window
        self.atr_window = atr_window
        self.atr_threshold = atr_threshold

    def on_candles(self, candles: Iterable[Candle]) -> StrategySignal:
        buffer: list[Candle] = list(candles)
        if len(buffer) < self.long_window + 1:
            return StrategySignal.HOLD

        closes = np.array([c.close for c in buffer], dtype=float)
        highs = np.array([c.high for c in buffer], dtype=float)
        lows = np.array([c.low for c in buffer], dtype=float)
        prev_closes = np.roll(closes, 1)

        short_ma = closes[-self.short_window :].mean()
        long_ma = closes[-self.long_window :].mean()

        prior_short_ma = closes[-self.short_window - 1 : -1].mean()
        prior_long_ma = closes[-self.long_window - 1 : -1].mean()

        if self.atr_window > 1:
            true_ranges = np.maximum(
                highs[1:] - lows[1:],
                np.maximum(
                    np.abs(highs[1:] - prev_closes[1:]),
                    np.abs(lows[1:] - prev_closes[1:]),
                ),
            )
            atr = true_ranges[-self.atr_window :].mean()
        else:
            atr = 0.0

        if atr < self.atr_threshold:
            return StrategySignal.HOLD

        crossed_up = prior_short_ma <= prior_long_ma and short_ma > long_ma
        crossed_down = prior_short_ma >= prior_long_ma and short_ma < long_ma

        if crossed_up:
            return StrategySignal.BUY
        if crossed_down:
            return StrategySignal.SELL
        return StrategySignal.HOLD
