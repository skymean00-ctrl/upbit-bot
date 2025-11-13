"""Trade history storage for persistence across server restarts."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class TradeHistoryStore:
    """SQLite-based trade history storage."""

    def __init__(self, db_path: str = "data/trade_history.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            market TEXT NOT NULL,
            strategy TEXT NOT NULL,
            signal TEXT NOT NULL,
            side TEXT NOT NULL,
            price REAL,
            volume REAL,
            amount REAL,
            order_id TEXT,
            order_response TEXT,
            dry_run BOOLEAN NOT NULL,
            balance_before REAL,
            balance_after REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            market TEXT NOT NULL,
            strategy TEXT NOT NULL,
            entry_price REAL NOT NULL,
            entry_volume REAL NOT NULL,
            entry_amount REAL NOT NULL,
            entry_time TEXT NOT NULL,
            exit_price REAL,
            exit_volume REAL,
            exit_amount REAL,
            exit_time TEXT,
            pnl REAL,
            pnl_pct REAL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        
        CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
        CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market);
        CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
        CREATE INDEX IF NOT EXISTS idx_positions_market ON positions(market);
        """
        with self._conn:
            self._conn.executescript(schema)

    def save_trade(
        self,
        market: str,
        strategy: str,
        signal: str,
        side: str,
        price: float | None = None,
        volume: float | None = None,
        amount: float | None = None,
        order_id: str | None = None,
        order_response: dict[str, Any] | None = None,
        dry_run: bool = True,
        balance_before: float | None = None,
        balance_after: float | None = None,
    ) -> int:
        """Save a trade record."""
        timestamp = datetime.now(UTC).isoformat()
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO trades (
                    timestamp, market, strategy, signal, side,
                    price, volume, amount, order_id, order_response,
                    dry_run, balance_before, balance_after
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    market,
                    strategy,
                    signal,
                    side,
                    price,
                    volume,
                    amount,
                    order_id,
                    json.dumps(order_response) if order_response else None,
                    dry_run,
                    balance_before,
                    balance_after,
                ),
            )
            return cursor.lastrowid

    def get_recent_trades(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent trades."""
        cursor = self._conn.execute(
            """
            SELECT * FROM trades
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_trades_by_market(self, market: str, limit: int = 100) -> list[dict[str, Any]]:
        """Get trades for a specific market."""
        cursor = self._conn.execute(
            """
            SELECT * FROM trades
            WHERE market = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (market, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_open_positions(self, market: str | None = None) -> list[dict[str, Any]]:
        """Get open positions."""
        if market:
            cursor = self._conn.execute(
                """
                SELECT * FROM positions
                WHERE status = 'open' AND market = ?
                ORDER BY entry_time DESC
                """,
                (market,),
            )
        else:
            cursor = self._conn.execute(
                """
                SELECT * FROM positions
                WHERE status = 'open'
                ORDER BY entry_time DESC
                """
            )
        return [dict(row) for row in cursor.fetchall()]

    def save_position(
        self,
        market: str,
        strategy: str,
        entry_price: float,
        entry_volume: float,
        entry_amount: float,
    ) -> int:
        """Save a new position."""
        entry_time = datetime.now(UTC).isoformat()
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO positions (
                    market, strategy, entry_price, entry_volume, entry_amount, entry_time
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (market, strategy, entry_price, entry_volume, entry_amount, entry_time),
            )
            return cursor.lastrowid

    def close_position(
        self,
        position_id: int,
        exit_price: float,
        exit_volume: float,
        exit_amount: float,
    ) -> None:
        """Close a position."""
        exit_time = datetime.now(UTC).isoformat()
        # Calculate PnL
        cursor = self._conn.execute(
            "SELECT entry_price, entry_amount FROM positions WHERE id = ?",
            (position_id,),
        )
        row = cursor.fetchone()
        if row:
            entry_price = row["entry_price"]
            entry_amount = row["entry_amount"]
            pnl = exit_amount - entry_amount
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100

            with self._conn:
                self._conn.execute(
                    """
                    UPDATE positions
                    SET exit_price = ?, exit_volume = ?, exit_amount = ?,
                        exit_time = ?, pnl = ?, pnl_pct = ?, status = 'closed'
                    WHERE id = ?
                    """,
                    (exit_price, exit_volume, exit_amount, exit_time, pnl, pnl_pct, position_id),
                )

    def get_statistics(self, market: str | None = None) -> dict[str, Any]:
        """Get trading statistics."""
        where_clause = "WHERE market = ?" if market else ""
        params = (market,) if market else ()

        # Total trades
        cursor = self._conn.execute(
            f"SELECT COUNT(*) as count FROM trades {where_clause}",
            params,
        )
        total_trades = cursor.fetchone()["count"]

        # Closed positions
        cursor = self._conn.execute(
            f"SELECT COUNT(*) as count, SUM(pnl) as total_pnl, AVG(pnl_pct) as avg_pnl_pct "
            f"FROM positions WHERE status = 'closed' {('AND market = ?' if market else '')}",
            params,
        )
        row = cursor.fetchone()
        closed_positions = row["count"] or 0
        total_pnl = row["total_pnl"] or 0.0
        avg_pnl_pct = row["avg_pnl_pct"] or 0.0

        # Winning trades
        cursor = self._conn.execute(
            f"SELECT COUNT(*) as count FROM positions "
            f"WHERE status = 'closed' AND pnl > 0 {('AND market = ?' if market else '')}",
            params,
        )
        winning_trades = cursor.fetchone()["count"] or 0

        win_rate = (winning_trades / closed_positions * 100) if closed_positions > 0 else 0.0

        return {
            "total_trades": total_trades,
            "closed_positions": closed_positions,
            "winning_trades": winning_trades,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_pnl_pct": avg_pnl_pct,
        }

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

