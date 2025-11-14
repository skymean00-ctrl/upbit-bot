"""Strategy factory for dynamic loading by name."""

from __future__ import annotations

from .ai_market_analyzer import AIMarketAnalyzer
from .ai_market_analyzer_high_risk import AIMarketAnalyzerHighRisk
from .base import Strategy
from .bb_squeeze import BBSqueezeStrategy
from .ma_crossover import MovingAverageCrossoverStrategy
from .macd_crossover import MACDCrossoverStrategy
from .mixed_bb_rsi_ma import MixedBBRSIMAStrategy
from .portfolio import CompositeStrategy
from .rsi_trend_filter import RSITrendFilterStrategy
from .support_resistance import SupportResistanceStrategy
from .volatility_breakout import VolatilityBreakoutStrategy
from .volume_profile import VolumeProfileStrategy

_REGISTRY: dict[str, type[Strategy]] = {
    MovingAverageCrossoverStrategy.name: MovingAverageCrossoverStrategy,
    RSITrendFilterStrategy.name: RSITrendFilterStrategy,
    VolatilityBreakoutStrategy.name: VolatilityBreakoutStrategy,
    MixedBBRSIMAStrategy.name: MixedBBRSIMAStrategy,
    MACDCrossoverStrategy.name: MACDCrossoverStrategy,
    BBSqueezeStrategy.name: BBSqueezeStrategy,
    SupportResistanceStrategy.name: SupportResistanceStrategy,
    VolumeProfileStrategy.name: VolumeProfileStrategy,
    AIMarketAnalyzer.name: AIMarketAnalyzer,
    AIMarketAnalyzerHighRisk.name: AIMarketAnalyzerHighRisk,
}


def get_strategy(name: str, **kwargs) -> Strategy:
    if name == CompositeStrategy.name:
        components = kwargs.pop("components", None)
        if not components:
            raise ValueError("Composite strategy requires 'components' argument.")
        strategies: list[tuple[Strategy, float]] = []
        for component in components:
            comp_name = component["name"]
            comp_weight = float(component.get("weight", 1.0))
            comp_params = component.get("params", {})
            if comp_name == CompositeStrategy.name:
                raise ValueError("Nested composite strategies are not supported.")
            strategies.append((get_strategy(comp_name, **comp_params), comp_weight))
        return CompositeStrategy(strategies)

    strategy_cls = _REGISTRY.get(name)
    if not strategy_cls:
        available = ", ".join(sorted(_REGISTRY))
        raise KeyError(f"Unknown strategy '{name}'. Available: {available}")
    return strategy_cls(**kwargs)


__all__ = ["get_strategy"]
