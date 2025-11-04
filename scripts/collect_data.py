#!/usr/bin/env python3
"""Collect real-time market data and persist to SQLite."""

from __future__ import annotations

import argparse
import asyncio
import logging

from upbit_bot.config import load_settings
from upbit_bot.data import MarketDataCollector, SqliteDataStore
from upbit_bot.utils.logging import configure_logging


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Upbit market data to SQLite.",
    )
    parser.add_argument("--env-file", help="Path to .env file for credential loading.")
    parser.add_argument(
        "--markets",
        nargs="+",
        default=None,
        help="Specific markets to collect (default: settings.market).",
    )
    parser.add_argument(
        "--db-path",
        default="data/upbit_marketdata.db",
        help="SQLite database path.",
    )
    parser.add_argument("--log-level", default=None, help="Override log level.")
    return parser.parse_args()


async def main_async() -> None:
    args = parse_args()
    configure_logging(level=args.log_level or "INFO")
    settings = load_settings(env_path=args.env_file)
    markets = args.markets or [settings.market]
    store = SqliteDataStore(db_path=args.db_path)
    collector = MarketDataCollector(markets=markets, store=store)
    try:
        await collector.start()
    finally:
        store.close()


def main() -> int:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Collector stopped by user.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
