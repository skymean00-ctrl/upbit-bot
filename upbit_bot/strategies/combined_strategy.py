from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from .base import Candle, Strategy, StrategySignal


class CombinedStrategy(Strategy):
    name = "combined_strategy"

    def __init__(self, sub_strategies: list[Strategy]) -> None:
        if not sub_strategies:
            raise ValueError("At least one sub-strategy is required.")
        self.sub_strategies = list(sub_strategies)

    def on_candles(self, candles: Iterable[Candle]) -> StrategySignal:
        signals = [strategy.on_candles(candles) for strategy in self.sub_strategies]
        if not signals:
            return StrategySignal.HOLD

        signal_counts = Counter(signals)
        buy_count = signal_counts.get(StrategySignal.BUY, 0)
        sell_count = signal_counts.get(StrategySignal.SELL, 0)
        hold_count = signal_counts.get(StrategySignal.HOLD, 0)

        if buy_count > sell_count and buy_count > hold_count:
            return StrategySignal.BUY
        if sell_count > buy_count and sell_count > hold_count:
            return StrategySignal.SELL
        return StrategySignal.HOLD
