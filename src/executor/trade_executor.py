"""Order execution pipeline that wires together strategy, risk and client."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List

from src.api.upbit_client import OrderRequest, UpbitClient
from src.risk.position_manager import PositionManager
from src.strategies.base import TradeSignal, TradingStrategy

LOGGER = logging.getLogger(__name__)


@dataclass
class ExecutionContext:
    market: str
    interval: str
    order_amount: float
    candle_count: int


class TradeExecutor:
    def __init__(
        self,
        client: UpbitClient,
        strategy: TradingStrategy,
        position_manager: PositionManager,
        context: ExecutionContext,
    ) -> None:
        self.client = client
        self.strategy = strategy
        self.position_manager = position_manager
        self.context = context

    def evaluate(self, candles: List[Dict[str, float]]) -> TradeSignal:
        closes = [float(candle["trade_price"]) for candle in candles]
        return self.strategy.generate_signal(closes)

    def execute(self, signal: TradeSignal, price: float) -> Dict:
        quantity = self._calculate_order_size(price)
        if not self.position_manager.can_open(signal, price, quantity):
            LOGGER.info("Risk limits blocked %s signal", signal)
            return {"status": "blocked", "signal": signal.value}

        side = "bid" if signal == TradeSignal.BUY else "ask"
        order = OrderRequest(
            market=self.context.market,
            side=side,
            volume=f"{quantity:.8f}",
            ord_type="market",
        )
        LOGGER.info("Placing %s order for %s (%s)", signal.value, quantity, self.context.market)
        response = self.client.place_order(order)
        self.position_manager.register_fill(signal, quantity, price)
        return response

    def _calculate_order_size(self, price: float) -> float:
        if price <= 0:
            return 0.0
        return self.context.order_amount / price


__all__ = ["ExecutionContext", "TradeExecutor"]
