"""Strategy interfaces and implementations."""

from .base import Candle, BaseStrategy, Strategy, StrategySignal
from .ma_crossover import MovingAverageCrossoverStrategy
from .rsi_trend_filter import RSITrendFilterStrategy
from .weighted_combined import WeightedCombinedStrategy

__all__ = [
    "Candle",
    "BaseStrategy",
    "Strategy",
    "StrategySignal",
    "MovingAverageCrossoverStrategy",
    "RSITrendFilterStrategy",
    "WeightedCombinedStrategy",
]
