"""Strategy abstractions."""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Sequence


class TradeSignal(str, Enum):
    BUY = "buy"
    SELL = "sell"
    HOLD = "hold"


class TradingStrategy(ABC):
    """Interface that all strategies must implement."""

    @abstractmethod
    def generate_signal(self, closes: Sequence[float]) -> TradeSignal:
        """Return a trade signal based on the provided closing prices."""


__all__ = ["TradeSignal", "TradingStrategy"]
