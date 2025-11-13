"""Bollinger Bands Squeeze strategy."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np

from .base import Candle, Strategy, StrategySignal


class BBSqueezeStrategy(Strategy):
    """Bollinger Bands Squeeze strategy."""

    name = "bb_squeeze"

    def __init__(
        self,
        period: int = 20,
        std_dev: float = 2.0,
        squeeze_threshold: float = 0.1,
        lookback: int = 10,
    ) -> None:
        self.period = period
        self.std_dev = std_dev
        self.squeeze_threshold = squeeze_threshold
        self.lookback = lookback

    def _bollinger_bands(self, closes: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Calculate Bollinger Bands."""
        ma = np.convolve(closes, np.ones(self.period) / self.period, mode="valid")
        std = np.array(
            [
                np.std(closes[i : i + self.period])
                for i in range(len(closes) - self.period + 1)
            ],
        )
        upper_band = ma + (std * self.std_dev)
        lower_band = ma - (std * self.std_dev)
        return upper_band, ma, lower_band

    def on_candles(self, candles: Iterable[Candle]) -> StrategySignal:
        buffer: list[Candle] = list(candles)
        if len(buffer) < self.period + self.lookback:
            return StrategySignal.HOLD

        closes = np.array([c.close for c in buffer], dtype=float)

        # Calculate Bollinger Bands
        upper_band, middle_band, lower_band = self._bollinger_bands(closes)

        if len(upper_band) < self.lookback + 1:
            return StrategySignal.HOLD

        # Calculate band width (squeeze indicator)
        band_width = (upper_band - lower_band) / middle_band

        # Check for squeeze (bands narrowing)
        current_band_width = band_width[-1]
        avg_band_width = np.mean(band_width[-self.lookback:-1])

        # Squeeze detected: bands are narrow
        is_squeeze = current_band_width < avg_band_width * (1 - self.squeeze_threshold)

        if not is_squeeze:
            return StrategySignal.HOLD

        # Check for expansion after squeeze
        if len(band_width) < 2:
            return StrategySignal.HOLD

        # Price breaks above upper band after squeeze
        if closes[-1] > upper_band[-1] and closes[-2] <= upper_band[-2]:
            return StrategySignal.BUY

        # Price breaks below lower band after squeeze
        if closes[-1] < lower_band[-1] and closes[-2] >= lower_band[-2]:
            return StrategySignal.SELL

        return StrategySignal.HOLD

