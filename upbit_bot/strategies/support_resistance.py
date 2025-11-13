"""Support/Resistance breakout strategy."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from .base import Candle, Strategy, StrategySignal


class SupportResistanceStrategy(Strategy):
    """Support/Resistance breakout strategy."""

    name = "support_resistance"

    def __init__(
        self,
        lookback: int = 50,
        min_touches: int = 2,
        breakout_threshold: float = 0.02,
    ) -> None:
        self.lookback = lookback
        self.min_touches = min_touches
        self.breakout_threshold = breakout_threshold

    def _find_support_resistance(self, highs: np.ndarray, lows: np.ndarray, closes: np.ndarray) -> tuple[float | None, float | None]:
        """Find support and resistance levels."""
        if len(highs) < self.lookback:
            return None, None

        recent_highs = highs[-self.lookback:]
        recent_lows = lows[-self.lookback:]
        recent_closes = closes[-self.lookback:]

        # Find resistance (local maxima)
        resistance_levels: list[float] = []
        for i in range(1, len(recent_highs) - 1):
            if recent_highs[i] > recent_highs[i - 1] and recent_highs[i] > recent_highs[i + 1]:
                resistance_levels.append(recent_highs[i])

        # Find support (local minima)
        support_levels: list[float] = []
        for i in range(1, len(recent_lows) - 1):
            if recent_lows[i] < recent_lows[i - 1] and recent_lows[i] < recent_lows[i + 1]:
                support_levels.append(recent_lows[i])

        # Get most recent resistance and support
        resistance = max(resistance_levels) if resistance_levels else None
        support = min(support_levels) if support_levels else None

        return support, resistance

    def on_candles(self, candles: Iterable[Candle]) -> StrategySignal:
        buffer: list[Candle] = list(candles)
        if len(buffer) < self.lookback + 1:
            return StrategySignal.HOLD

        closes = np.array([c.close for c in buffer], dtype=float)
        highs = np.array([c.high for c in buffer], dtype=float)
        lows = np.array([c.low for c in buffer], dtype=float)

        support, resistance = self._find_support_resistance(highs, lows, closes)

        if support is None or resistance is None:
            return StrategySignal.HOLD

        current_price = closes[-1]
        prev_price = closes[-2]

        # Breakout above resistance
        if prev_price <= resistance and current_price > resistance * (1 + self.breakout_threshold):
            return StrategySignal.BUY

        # Breakdown below support
        if prev_price >= support and current_price < support * (1 - self.breakout_threshold):
            return StrategySignal.SELL

        return StrategySignal.HOLD

