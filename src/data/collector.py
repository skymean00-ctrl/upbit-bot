"""Market data collection utilities."""
from __future__ import annotations

import logging
from typing import Dict, List

from src.api.upbit_client import UpbitClient

LOGGER = logging.getLogger(__name__)


class MarketDataCollector:
    """Wrapper that isolates REST calls from business logic."""

    def __init__(self, client: UpbitClient) -> None:
        self._client = client

    def get_recent_candles(self, market: str, interval: str, count: int) -> List[Dict]:
        LOGGER.debug("Fetching %s candles for %s", interval, market)
        return self._client.fetch_candles(interval=interval, market=market, count=count)

    def get_latest_price(self, market: str) -> float:
        ticker = self._client.fetch_ticker(market)
        return float(ticker["trade_price"])


__all__ = ["MarketDataCollector"]
