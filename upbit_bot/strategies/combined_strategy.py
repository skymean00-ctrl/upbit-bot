from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from pydantic import Field

from .base import BaseStrategy, StrategySignal

if TYPE_CHECKING:
    from .base import Candle


class CombinedStrategy(BaseStrategy):
    name = "combined_strategy"

    sub_strategies: list[BaseStrategy]

    def __init__(self, sub_strategies: list[BaseStrategy], **data):
        super().__init__(**data)
        if not all(isinstance(s, BaseStrategy) for s in sub_strategies):
            raise TypeError("All sub_strategies must be instances of BaseStrategy.")
        self.sub_strategies = sub_strategies

    def determine_signal(self, candles: list[Candle]) -> StrategySignal:
        if not self.sub_strategies:
            return StrategySignal.HOLD

        signals = []
        for strat in self.sub_strategies:
            signals.append(strat.determine_signal(candles))

        signal_counts = Counter(signals)

        buy_count = signal_counts.get(StrategySignal.BUY, 0)
        sell_count = signal_counts.get(StrategySignal.SELL, 0)
        hold_count = signal_counts.get(StrategySignal.HOLD, 0)

        if buy_count > sell_count and buy_count > hold_count:
            return StrategySignal.BUY
        elif sell_count > buy_count and sell_count > hold_count:
            return StrategySignal.SELL
        else:
            return StrategySignal.HOLD

