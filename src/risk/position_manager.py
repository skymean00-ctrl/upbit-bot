"""Risk and position management helpers."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from src.strategies.base import TradeSignal


@dataclass
class Position:
    side: Optional[TradeSignal] = None
    quantity: float = 0.0
    entry_price: float = 0.0
    timestamp: float = 0.0


class PositionManager:
    """Tracks open exposure and enforces notional limits."""

    def __init__(self, max_position: float, max_notional: float) -> None:
        self.max_position = max_position
        self.max_notional = max_notional
        self.position = Position()

    def can_open(self, signal: TradeSignal, price: float, order_size: float) -> bool:
        if signal == TradeSignal.HOLD:
            return False
        if order_size <= 0:
            return False
        if order_size > self.max_position:
            return False
        if order_size * price > self.max_notional:
            return False
        return True

    def register_fill(self, signal: TradeSignal, quantity: float, price: float) -> None:
        timestamp = time.time()
        if signal == TradeSignal.BUY:
            self.position = Position(side=signal, quantity=quantity, entry_price=price, timestamp=timestamp)
        elif signal == TradeSignal.SELL:
            self.position = Position(side=signal, quantity=quantity, entry_price=price, timestamp=timestamp)
        else:
            self.position = Position()

    def close_position(self) -> None:
        self.position = Position()


__all__ = ["Position", "PositionManager"]
