"""Strategy interfaces and implementations."""

from .ai_market_analyzer import AIMarketAnalyzer
from .base import Candle, Strategy, StrategySignal
from .bb_squeeze import BBSqueezeStrategy
from .combined_strategy import CombinedStrategy
from .portfolio import CompositeStrategy
from .factory import get_strategy
from .ma_crossover import MovingAverageCrossoverStrategy
from .macd_crossover import MACDCrossoverStrategy
from .mixed_bb_rsi_ma import MixedBBRSIMAStrategy
from .rsi_trend_filter import RSITrendFilterStrategy
from .support_resistance import SupportResistanceStrategy
from .volatility_breakout import VolatilityBreakoutStrategy
from .volume_profile import VolumeProfileStrategy

__all__ = [
    "Candle",
    "Strategy",
    "StrategySignal",
    "CombinedStrategy",
    "get_strategy",
    "CompositeStrategy",
    "MovingAverageCrossoverStrategy",
    "MixedBBRSIMAStrategy",
    "RSITrendFilterStrategy",
    "VolatilityBreakoutStrategy",
    "MACDCrossoverStrategy",
    "BBSqueezeStrategy",
    "SupportResistanceStrategy",
    "VolumeProfileStrategy",
    "AIMarketAnalyzer",
]
