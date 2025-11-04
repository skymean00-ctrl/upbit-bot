"""Composite strategy helpers for portfolio allocation."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .base import Candle, Strategy, StrategySignal


@dataclass
class WeightedStrategy:
    strategy: Strategy
    weight: float


class CompositeStrategy(Strategy):
    """Combines multiple strategies and aggregates signals by weight."""

    name = "composite"

    def __init__(self, strategies: Iterable[tuple[Strategy, float]]) -> None:
        pairs = list(strategies)
        if not pairs:
            raise ValueError("At least one strategy required for composite strategy.")
        total_weight = sum(weight for _, weight in pairs)
        if total_weight <= 0:
            raise ValueError("Total weight must be positive.")
        self.strategies: list[WeightedStrategy] = [
            WeightedStrategy(strategy=strategy, weight=weight / total_weight)
            for strategy, weight in pairs
        ]

    def on_candles(self, candles: Iterable[Candle]) -> StrategySignal:
        score = 0.0
        for item in self.strategies:
            signal = item.strategy.on_candles(candles)
            if signal is StrategySignal.BUY:
                score += item.weight
            elif signal is StrategySignal.SELL:
                score -= item.weight
        if score > 0.1:
            return StrategySignal.BUY
        if score < -0.1:
            return StrategySignal.SELL
        return StrategySignal.HOLD
