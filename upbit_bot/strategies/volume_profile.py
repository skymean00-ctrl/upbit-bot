"""Volume Profile strategy."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from .base import Candle, Strategy, StrategySignal


class VolumeProfileStrategy(Strategy):
    """Volume Profile strategy based on volume concentration."""

    name = "volume_profile"

    def __init__(
        self,
        period: int = 20,
        volume_threshold: float = 1.5,
        price_threshold: float = 0.02,
    ) -> None:
        self.period = period
        self.volume_threshold = volume_threshold
        self.price_threshold = price_threshold

    def _calculate_poc(self, candles: list[Candle]) -> float | None:
        """Calculate Point of Control (POC) - price with highest volume."""
        if len(candles) < self.period:
            return None

        recent_candles = candles[-self.period:]
        volumes = np.array([c.volume for c in recent_candles])
        closes = np.array([c.close for c in recent_candles])

        # Weighted average price by volume
        total_volume = np.sum(volumes)
        if total_volume == 0:
            return None

        poc = np.sum(closes * volumes) / total_volume
        return float(poc)

    def on_candles(self, candles: Iterable[Candle]) -> StrategySignal:
        buffer: list[Candle] = list(candles)
        if len(buffer) < self.period + 1:
            return StrategySignal.HOLD

        # Calculate POC
        poc = self._calculate_poc(buffer)
        if poc is None:
            return StrategySignal.HOLD

        # Get recent volume
        recent_volumes = np.array([c.volume for c in buffer[-self.period:]])
        avg_volume = np.mean(recent_volumes[:-1])
        current_volume = recent_volumes[-1]

        # Check for volume spike
        volume_spike = current_volume > avg_volume * self.volume_threshold

        if not volume_spike:
            return StrategySignal.HOLD

        current_price = buffer[-1].close
        prev_price = buffer[-2].close

        # Price breaks above POC with high volume
        if prev_price <= poc and current_price > poc * (1 + self.price_threshold):
            return StrategySignal.BUY

        # Price breaks below POC with high volume
        if prev_price >= poc and current_price < poc * (1 - self.price_threshold):
            return StrategySignal.SELL

        return StrategySignal.HOLD

