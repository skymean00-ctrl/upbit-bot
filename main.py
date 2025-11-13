"""Command line entry-point for the Upbit bot."""
from __future__ import annotations

import argparse
import asyncio
import logging
from typing import Optional

from src.api.upbit_client import UpbitClient
from src.config import AppSettings, load_settings
from src.data.collector import MarketDataCollector
from src.executor.trade_executor import ExecutionContext, TradeExecutor
from src.risk.position_manager import PositionManager
from src.strategies.moving_average import MovingAverageStrategy


LOGGER = logging.getLogger(__name__)


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")


def build_executor(settings: AppSettings) -> TradeExecutor:
    client = UpbitClient(settings.access_key, settings.secret_key)
    strategy = MovingAverageStrategy(settings.strategy.moving_average)
    position_manager = PositionManager(
        max_position=settings.risk.max_position,
        max_notional=settings.risk.max_notional,
    )
    context = ExecutionContext(
        market=settings.market,
        interval=settings.candle_interval,
        order_amount=settings.order_amount,
        candle_count=settings.candle_count,
    )
    return TradeExecutor(client, strategy, position_manager, context)


async def run_loop(executor: TradeExecutor, collector: MarketDataCollector, settings: AppSettings) -> None:
    LOGGER.info("Starting trading loop for %s", settings.market)
    while True:
        candles = collector.get_recent_candles(settings.market, settings.candle_interval, settings.candle_count)
        signal = executor.evaluate(candles)
        last_price = float(candles[-1]["trade_price"])
        LOGGER.info("Generated %s signal at price %s", signal, last_price)
        try:
            result = executor.execute(signal, last_price)
            LOGGER.debug("Execution result: %s", result)
        except Exception as exc:  # pragma: no cover - runtime safety net
            LOGGER.exception("Order execution failed: %s", exc)
        await asyncio.sleep(settings.poll_interval)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Upbit trading bot")
    parser.add_argument("--config", help="Path to JSON config file", default=None)
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    configure_logging(args.verbose)
    settings = load_settings(args.config)
    executor = build_executor(settings)
    collector = MarketDataCollector(executor.client)
    asyncio.run(run_loop(executor, collector, settings))


if __name__ == "__main__":  # pragma: no cover
    main()
