"""Realtime market data collector using Upbit websocket."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable

import aiohttp

from .storage import DataStore

LOGGER = logging.getLogger(__name__)


class CollectorShutdownError(Exception):
    """Raised when collector should stop."""


class MarketDataCollector:
    """Collects trades and orderbook updates for a set of markets."""

    WS_URL = "wss://api.upbit.com/websocket/v1"

    def __init__(self, markets: Iterable[str], store: DataStore, reconnect_delay: int = 5) -> None:
        self.markets: list[str] = list(markets)
        if not self.markets:
            raise ValueError("At least one market must be provided.")
        self.store = store
        self.reconnect_delay = reconnect_delay
        self._session: aiohttp.ClientSession | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        self._stop_event.clear()
        while not self._stop_event.is_set():
            try:
                await self._run_once()
            except CollectorShutdownError:
                LOGGER.info("Collector shutdown requested.")
                break
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Collector crashed: %s", exc)
                await asyncio.sleep(self.reconnect_delay)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._session and not self._session.closed:
            await self._session.close()

    async def _run_once(self) -> None:
        payload = [
            {"ticket": "UNIQUE_TICKET"},
            {"type": "trade", "codes": self.markets},
            {"type": "orderbook", "codes": self.markets},
            {"format": "SIMPLE"},
        ]
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession()

        async with self._session.ws_connect(self.WS_URL, heartbeat=15) as ws:
            await ws.send_str(json.dumps(payload))
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    await self._handle_message(msg.data)
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    await self._handle_message(msg.data.decode("utf-8"))
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    LOGGER.error("Websocket error: %s", msg.data)
                    break
                elif msg.type in {aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING}:
                    LOGGER.info("Websocket closed.")
                    break

    async def _handle_message(self, data: str) -> None:
        message = json.loads(data)
        msg_type = message.get("type")
        if msg_type == "trade":
            await self.store.store_trade(message)
        elif msg_type == "orderbook":
            await self.store.store_orderbook(message)
        else:
            LOGGER.debug("Unhandled message type: %s", msg_type)
