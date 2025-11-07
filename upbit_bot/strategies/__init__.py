"""Strategy interfaces and implementations."""

from .base import Candle, BaseStrategy, StrategySignal
from .combined_strategy import CombinedStrategy
from .factory import get_strategy
from .ma_crossover import MovingAverageCrossoverStrategy
from .mixed_bb_rsi_ma import MixedBBRSIMAStrategy
from .rsi_trend_filter import RSITrendFilterStrategy
from .volatility_breakout import VolatilityBreakoutStrategy

__all__ = [
    "Candle",
    "BaseStrategy",
    "StrategySignal",
    "CombinedStrategy",
    "get_strategy",
    "MovingAverageCrossoverStrategy",
    "MixedBBRSIMAStrategy",
    "RSITrendFilterStrategy",
    "VolatilityBreakoutStrategy",
]
