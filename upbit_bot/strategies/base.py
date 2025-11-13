"""Strategy abstractions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum


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


class Strategy(ABC):
    """Base class that concrete strategies extend for signal generation."""

    name: str

    @abstractmethod
    def on_candles(self, candles: Iterable[Candle]) -> StrategySignal:
        """Return the next signal based on incoming candles."""


BaseStrategy = Strategy
