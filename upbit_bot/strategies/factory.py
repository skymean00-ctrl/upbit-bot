"""Strategy factory for dynamic loading by name."""

from __future__ import annotations

from .base import Strategy
from .ma_crossover import MovingAverageCrossoverStrategy
from .portfolio import CompositeStrategy
from .rsi_trend_filter import RSITrendFilterStrategy

_REGISTRY: dict[str, type[Strategy]] = {
    MovingAverageCrossoverStrategy.name: MovingAverageCrossoverStrategy,
    RSITrendFilterStrategy.name: RSITrendFilterStrategy,
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
