"""Execution loop for polling-based trading."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from threading import Event, Thread

from upbit_bot.core import UpbitClient
from upbit_bot.strategies import Candle, Strategy, StrategySignal
from upbit_bot.utils.notifications import Notifier

from .risk import PositionSizer, RiskManager

LOGGER = logging.getLogger(__name__)


class ExecutionEngine:
    """Simple polling-based execution engine for a single market."""

    def __init__(
        self,
        client: UpbitClient,
        strategy: Strategy,
        market: str,
        candle_unit: int = 1,
        candle_count: int = 200,
        poll_interval: int = 30,
        dry_run: bool = True,
        order_amount: float | None = None,
        risk_manager: RiskManager | None = None,
        position_sizer: PositionSizer | None = None,
        notifiers: Sequence[Notifier] | None = None,
        min_order_amount: float = 5000.0,
    ) -> None:
        self.client = client
        self.strategy = strategy
        self.market = market
        self.candle_unit = candle_unit
        self.candle_count = candle_count
        self.poll_interval = poll_interval
        self.dry_run = dry_run
        self.order_amount = order_amount
        self.risk_manager = risk_manager
        self.position_sizer = position_sizer
        self.notifiers = list(notifiers or [])
        self.position_price: float | None = None
        self.position_volume: float | None = None
        self.min_order_amount = max(min_order_amount, 0.0)
        self._stop_event: Event = Event()
        self._worker: Thread | None = None
        self.last_signal: StrategySignal | None = None
        self.last_order_info: dict | None = None
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None

    def _fetch_candles(self) -> list[Candle]:
        raw = self.client.get_candles(self.market, unit=self.candle_unit, count=self.candle_count)
        candles = [
            Candle(
                timestamp=int(item["timestamp"]),
                open=float(item["opening_price"]),
                high=float(item["high_price"]),
                low=float(item["low_price"]),
                close=float(item["trade_price"]),
                volume=float(item["candle_acc_trade_volume"]),
            )
            for item in reversed(raw)
        ]
        return candles

    def _determine_order_amount(self) -> float:
        if self.position_sizer:
            stake = self.position_sizer.krw_stake()
        elif self.order_amount is not None:
            stake = self.order_amount
        else:
            raise ValueError("order_amount must be provided when no position_sizer is configured.")
        return max(stake, self.min_order_amount)

    def _notify(self, message: str, **kwargs) -> None:
        for notifier in self.notifiers:
            notifier.send(message, **kwargs)

    def _execute_signal(self, signal: StrategySignal, candles: list[Candle]) -> dict | None:
        self.last_order_info = None
        if signal is StrategySignal.HOLD:
            LOGGER.debug("No action taken for market %s", self.market)
            return None

        last_candle = candles[-1]
        side = "bid" if signal is StrategySignal.BUY else "ask"
        LOGGER.info(
            "Signal %s -> %s for %s (price %.0f)",
            self.strategy.name,
            signal.value,
            self.market,
            last_candle.close,
        )

        if signal is StrategySignal.BUY:
            if self.position_price is not None:
                LOGGER.debug("Position already open for %s; ignoring BUY signal.", self.market)
                return None
            if self.risk_manager and not self.risk_manager.can_open_position(self.market):
                return None
            stake = self._determine_order_amount()
            est_volume = stake / last_candle.close if last_candle.close else 0.0
            if self.dry_run:
                LOGGER.info(
                    "Dry-run buy: market=%s stake=%.0fKRW volume~%.6f",
                    self.market,
                    stake,
                    est_volume,
                )
                self.position_price = last_candle.close
                self.position_volume = est_volume
                if self.risk_manager:
                    balance = (
                        self.position_sizer.balance_fetcher() if self.position_sizer else stake
                    )
                    stake_pct = (stake / balance * 100) if balance else 0.0
                    self.risk_manager.register_entry(self.market, stake_pct=stake_pct)
                info = {
                    "status": "dry_run",
                    "signal": "buy",
                    "stake": stake,
                    "price": last_candle.close,
                }
                self.last_order_info = info
                self._notify(
                    "Dry-run BUY executed",
                    market=self.market,
                    stake=stake,
                    price=last_candle.close,
                )
                return info

            response = self.client.place_order(
                self.market,
                side=side,
                ord_type="price",
                price=f"{stake:.0f}",
            )
            LOGGER.debug("Order response: %s", response)
            self.position_price = last_candle.close
            self.position_volume = est_volume
            if self.risk_manager:
                balance = self.position_sizer.balance_fetcher() if self.position_sizer else stake
                stake_pct = (stake / balance * 100) if balance else 0.0
                self.risk_manager.register_entry(self.market, stake_pct=stake_pct)
            self.last_order_info = response
            self._notify(
                "Live BUY placed",
                market=self.market,
                stake=stake,
                response=response,
            )
            return response

        if self.position_price is None:
            LOGGER.debug("No open position for %s; ignoring SELL signal.", self.market)
            return None

        pnl_pct = (
            (last_candle.close - self.position_price) / self.position_price * 100
            if self.position_price
            else 0.0
        )
        if self.dry_run:
            LOGGER.info(
                "Dry-run sell: market=%s price=%.0f pnl=%.2f%%",
                self.market,
                last_candle.close,
                pnl_pct,
            )
            if self.risk_manager:
                self.risk_manager.register_exit(self.market, pnl_pct=pnl_pct)
            info = {
                "status": "dry_run",
                "signal": "sell",
                "price": last_candle.close,
                "pnl_pct": pnl_pct,
            }
            self.last_order_info = info
            self._notify(
                "Dry-run SELL executed",
                market=self.market,
                price=last_candle.close,
                pnl_pct=pnl_pct,
            )
            self.position_price = None
            self.position_volume = None
            return info

        volume = self.position_volume or 0.0
        if volume <= 0:
            order_amount = self._determine_order_amount()
            volume = order_amount / last_candle.close if last_candle.close else 0.0
        response = self.client.place_order(
            self.market,
            side=side,
            ord_type="market",
            volume=f"{volume}",
        )
        LOGGER.debug("Order response: %s", response)
        if self.risk_manager:
            self.risk_manager.register_exit(self.market, pnl_pct=pnl_pct)
        self.last_order_info = response
        self._notify(
            "Live SELL placed",
            market=self.market,
            price=last_candle.close,
            response=response,
            pnl_pct=pnl_pct,
        )
        self.position_price = None
        self.position_volume = None
        return response

    def run_once(self) -> dict | None:
        candles = self._fetch_candles()
        signal = self.strategy.on_candles(candles)
        LOGGER.info("Strategy %s signal: %s", self.strategy.name, signal.value)
        self.last_signal = signal
        self.last_run_at = datetime.now(UTC)
        self.last_error = None
        try:
            result = self._execute_signal(signal, candles)
            return result
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            raise

    def run_forever(self) -> None:
        LOGGER.info("Starting execution loop for %s", self.market)
        self._stop_event.clear()
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Execution step failed: %s", exc)
                if self.last_error is None:
                    self.last_error = str(exc)
            if self._stop_event.wait(self.poll_interval):
                break
        LOGGER.info("Execution loop for %s stopped", self.market)

    def start_async(self) -> Thread:
        if self._worker and self._worker.is_alive():
            return self._worker
        self._stop_event.clear()
        self._worker = Thread(target=self.run_forever, daemon=True)
        self._worker.start()
        return self._worker

    def stop(self, join: bool = False, timeout: float | None = None) -> None:
        self._stop_event.set()
        if join and self._worker and self._worker.is_alive():
            self._worker.join(timeout=timeout)

    def is_running(self) -> bool:
        return (
            self._worker is not None
            and self._worker.is_alive()
            and not self._stop_event.is_set()
        )
