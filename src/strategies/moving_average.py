"""Simple moving average crossover strategy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from src.strategies.base import TradeSignal, TradingStrategy


@dataclass
class MovingAverageConfig:
    short_window: int = 5
    long_window: int = 20

    def __post_init__(self) -> None:
        if self.short_window >= self.long_window:
            raise ValueError("short_window must be less than long_window")


class MovingAverageStrategy(TradingStrategy):
    def __init__(self, config: MovingAverageConfig) -> None:
        self.config = config

    def generate_signal(self, closes: Sequence[float]) -> TradeSignal:
        if len(closes) < self.config.long_window:
            return TradeSignal.HOLD

        short_avg = sum(closes[-self.config.short_window :]) / self.config.short_window
        long_avg = sum(closes[-self.config.long_window :]) / self.config.long_window

        if short_avg > long_avg:
            return TradeSignal.BUY
        if short_avg < long_avg:
            return TradeSignal.SELL
        return TradeSignal.HOLD


__all__ = ["MovingAverageConfig", "MovingAverageStrategy"]
