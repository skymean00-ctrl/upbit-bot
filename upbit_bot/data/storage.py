"""Storage backends for collected market data."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path
from typing import Any


class DataStore(ABC):
    """Interface for persisting market data."""

    @abstractmethod
    async def store_trade(self, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def store_orderbook(self, payload: dict[str, Any]) -> None:
        raise NotImplementedError


class SqliteDataStore(DataStore):
    """SQLite-backed data store suitable for local research."""

    def __init__(self, db_path: str = "data/upbit_marketdata.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        self._init_schema()

    def _init_schema(self) -> None:
        trade_schema = """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            trade_price REAL NOT NULL,
            trade_volume REAL NOT NULL,
            ask_bid TEXT NOT NULL,
            sequential_id INTEGER NOT NULL,
            raw_payload TEXT NOT NULL
        );
        """
        orderbook_schema = """
        CREATE TABLE IF NOT EXISTS orderbooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            total_ask_size REAL,
            total_bid_size REAL,
            orderbook_units TEXT NOT NULL,
            raw_payload TEXT NOT NULL
        );
        """
        with self._conn:
            self._conn.executescript(trade_schema + orderbook_schema)

    async def store_trade(self, payload: dict[str, Any]) -> None:
        await self._execute(
            """
            INSERT INTO trades (
                market,
                timestamp,
                trade_price,
                trade_volume,
                ask_bid,
                sequential_id,
                raw_payload
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("code"),
                payload.get("timestamp"),
                payload.get("trade_price"),
                payload.get("trade_volume"),
                payload.get("ask_bid"),
                payload.get("sequential_id"),
                json.dumps(payload),
            ),
        )

    async def store_orderbook(self, payload: dict[str, Any]) -> None:
        await self._execute(
            """
            INSERT INTO orderbooks (
                market,
                timestamp,
                total_ask_size,
                total_bid_size,
                orderbook_units,
                raw_payload
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                payload.get("code"),
                payload.get("timestamp"),
                payload.get("total_ask_size"),
                payload.get("total_bid_size"),
                json.dumps(payload.get("orderbook_units")),
                json.dumps(payload),
            ),
        )

    async def _execute(self, query: str, params: Iterable[Any]) -> None:
        def _run() -> None:
            with self._conn:
                self._conn.execute(query, tuple(params))

        await self._loop.run_in_executor(None, _run)

    def close(self) -> None:
        self._conn.close()
