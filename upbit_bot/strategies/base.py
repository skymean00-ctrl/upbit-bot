"""Strategy abstractions."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class StrategySignal(str, Enum):
    HOLD = "hold"
    BUY = "buy"
    SELL = "sell"


class BaseStrategy(Protocol):
    """Callable protocol used by the execution engine."""

    name: str

    def on_candles(self, candles: Iterable[Candle]) -> StrategySignal:
        """Return the next signal based on incoming candles."""


# Backward compatibility alias
Strategy = BaseStrategy
