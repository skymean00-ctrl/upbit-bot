"""Strategy interfaces and implementations."""

from .base import Candle, Strategy, StrategySignal
from .factory import get_strategy
from .ma_crossover import MovingAverageCrossoverStrategy
from .portfolio import CompositeStrategy
from .rsi_trend_filter import RSITrendFilterStrategy

__all__ = [
    "Candle",
    "Strategy",
    "StrategySignal",
    "MovingAverageCrossoverStrategy",
    "CompositeStrategy",
    "RSITrendFilterStrategy",
    "get_strategy",
]
