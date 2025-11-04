from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

from upbit_bot.services.execution import ExecutionEngine
from upbit_bot.services.risk import PositionSizer, RiskConfig
from upbit_bot.strategies import Candle, Strategy, StrategySignal
from upbit_bot.web.controller import TradingController


class DummyClient:
    def __init__(self) -> None:
        self.orders: list[dict[str, Any]] = []
        self.accounts: list[dict[str, Any]] = [
            {
                "currency": "KRW",
                "balance": "100000",
                "locked": "0",
                "avg_buy_price": "0",
            }
        ]

    def get_candles(self, market: str, unit: int, count: int) -> list[dict[str, Any]]:
        return [
            {
                "timestamp": 0,
                "opening_price": 10000,
                "high_price": 10000,
                "low_price": 10000,
                "trade_price": 10000,
                "candle_acc_trade_volume": 1,
            }
        ]

    def place_order(self, market: str, side: str, ord_type: str, **kwargs: Any) -> dict[str, Any]:
        order = {"market": market, "side": side, "ord_type": ord_type, **kwargs}
        self.orders.append(order)
        return order

    def get_accounts(self) -> list[dict[str, Any]]:
        return self.accounts


class DummyStrategy(Strategy):
    name = "dummy"

    def __init__(self, signal: StrategySignal = StrategySignal.HOLD) -> None:
        self._signal = signal

    def on_candles(self, candles: Iterable[Candle]) -> StrategySignal:
        return self._signal


def test_min_order_amount_enforced_without_sizer() -> None:
    client = DummyClient()
    strategy = DummyStrategy(StrategySignal.HOLD)
    engine = ExecutionEngine(
        client=client,
        strategy=strategy,
        market="KRW-BTC",
        dry_run=True,
        order_amount=1000,
        min_order_amount=5000,
    )
    assert engine._determine_order_amount() == 5000  # noqa: SLF001


def test_min_order_amount_enforced_with_position_sizer() -> None:
    client = DummyClient()
    strategy = DummyStrategy(StrategySignal.HOLD)
    sizer = PositionSizer(balance_fetcher=lambda: 100_000, config=RiskConfig(max_position_pct=1))
    engine = ExecutionEngine(
        client=client,
        strategy=strategy,
        market="KRW-BTC",
        dry_run=True,
        position_sizer=sizer,
        min_order_amount=5000,
    )
    assert engine._determine_order_amount() == 5000  # noqa: SLF001


def test_engine_thread_start_and_stop() -> None:
    client = DummyClient()
    strategy = DummyStrategy(StrategySignal.HOLD)
    engine = ExecutionEngine(
        client=client,
        strategy=strategy,
        market="KRW-BTC",
        dry_run=True,
        order_amount=6000,
        poll_interval=0.05,
        min_order_amount=5000,
    )
    engine.start_async()
    time.sleep(0.1)
    assert engine.is_running()
    engine.stop(join=True)
    assert not engine.is_running()


def test_trading_controller_state_and_balance() -> None:
    client = DummyClient()
    strategy = DummyStrategy(StrategySignal.HOLD)
    engine = ExecutionEngine(
        client=client,
        strategy=strategy,
        market="KRW-BTC",
        dry_run=True,
        order_amount=6000,
        poll_interval=0.05,
    )
    controller = TradingController(engine=engine, client=client)
    state = controller.get_state()
    assert not state.running
    assert state.market == "KRW-BTC"
    balance = controller.get_account_overview()
    assert balance["krw_balance"] == 100000.0
