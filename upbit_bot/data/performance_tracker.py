"""Performance tracking and analysis for trading bot."""

from __future__ import annotations

import sqlite3
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class PerformanceTracker:
    """SQLite-based performance analysis."""

    def __init__(self, db_path: str = "data/performance.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._init_schema()

    def _init_schema(self) -> None:
        """Initialize database schema."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                strategy TEXT NOT NULL,
                market TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_price REAL NOT NULL,
                quantity REAL NOT NULL,
                profit_loss REAL NOT NULL,
                profit_loss_pct REAL NOT NULL,
                trade_duration_minutes INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        self._conn.commit()

    def record_trade(
        self,
        strategy: str,
        market: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
        trade_duration_minutes: int = 0,
    ) -> int:
        """Record a completed trade."""
        profit_loss = (exit_price - entry_price) * quantity
        profit_loss_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

        cursor = self._conn.execute(
            """
            INSERT INTO performance 
            (date, strategy, market, entry_price, exit_price, quantity, 
             profit_loss, profit_loss_pct, trade_duration_minutes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(UTC).strftime("%Y-%m-%d"),
                strategy,
                market,
                entry_price,
                exit_price,
                quantity,
                profit_loss,
                profit_loss_pct,
                trade_duration_minutes,
                datetime.now(UTC).isoformat(),
            ),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_statistics(
        self, strategy: str | None = None, market: str | None = None, days: int = 0
    ) -> dict[str, Any]:
        """Calculate performance statistics."""
        query = "SELECT * FROM performance WHERE 1=1"
        params: list[Any] = []

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)

        if market:
            query += " AND market = ?"
            params.append(market)

        if days > 0:
            query += " AND date >= date('now', '-' || ? || ' days')"
            params.append(days)

        query += " ORDER BY created_at DESC"

        cursor = self._conn.execute(query, params)
        trades = cursor.fetchall()

        if not trades:
            return self._empty_stats()

        # Convert rows to dicts
        trades_list = [dict(row) for row in trades]

        # Calculate statistics
        profit_losses = [t["profit_loss"] for t in trades_list]
        profit_loss_pcts = [t["profit_loss_pct"] for t in trades_list]
        winning_trades = [t for t in trades_list if t["profit_loss"] > 0]
        losing_trades = [t for t in trades_list if t["profit_loss"] < 0]

        total_profit_loss = sum(profit_losses)
        win_count = len(winning_trades)
        lose_count = len(losing_trades)
        total_trades = len(trades_list)
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0

        # Average profit per trade
        avg_profit_loss = total_profit_loss / total_trades if total_trades > 0 else 0
        avg_win = sum(t["profit_loss"] for t in winning_trades) / win_count if win_count > 0 else 0
        avg_loss = sum(t["profit_loss"] for t in losing_trades) / lose_count if lose_count > 0 else 0

        # Profit factor
        total_wins = sum(t["profit_loss"] for t in winning_trades)
        total_losses = abs(sum(t["profit_loss"] for t in losing_trades))
        profit_factor = total_wins / total_losses if total_losses > 0 else 0

        # Maximum drawdown (simple calculation)
        cumulative_pnl = 0
        peak = 0
        max_drawdown = 0
        for trade in trades_list:
            cumulative_pnl += trade["profit_loss"]
            if cumulative_pnl > peak:
                peak = cumulative_pnl
            drawdown = peak - cumulative_pnl
            if drawdown > max_drawdown:
                max_drawdown = drawdown

        # Sharpe Ratio (simplified - requires std dev of returns)
        if len(profit_loss_pcts) > 1 and statistics.stdev(profit_loss_pcts) > 0:
            avg_return = statistics.mean(profit_loss_pcts)
            std_return = statistics.stdev(profit_loss_pcts)
            sharpe_ratio = (avg_return / std_return) * (252 ** 0.5) if std_return > 0 else 0
        else:
            sharpe_ratio = 0

        # Average trade duration
        durations = [t["trade_duration_minutes"] for t in trades_list if t["trade_duration_minutes"] > 0]
        avg_duration = statistics.mean(durations) if durations else 0

        return {
            "total_trades": total_trades,
            "winning_trades": win_count,
            "losing_trades": lose_count,
            "win_rate": round(win_rate, 2),
            "total_profit_loss": round(total_profit_loss, 2),
            "avg_profit_loss": round(avg_profit_loss, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown": round(max_drawdown, 2),
            "sharpe_ratio": round(sharpe_ratio, 2),
            "avg_trade_duration_minutes": round(avg_duration, 2),
            "avg_profit_loss_pct": round(statistics.mean(profit_loss_pcts), 2) if profit_loss_pcts else 0,
            "trades": trades_list[:50],  # Last 50 trades for display
        }

    def _empty_stats(self) -> dict[str, Any]:
        """Return empty statistics."""
        return {
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0.0,
            "total_profit_loss": 0.0,
            "avg_profit_loss": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "sharpe_ratio": 0.0,
            "avg_trade_duration_minutes": 0.0,
            "avg_profit_loss_pct": 0.0,
            "trades": [],
        }

    def get_daily_stats(self, strategy: str | None = None) -> list[dict[str, Any]]:
        """Get daily performance statistics."""
        query = """
            SELECT 
                date,
                strategy,
                COUNT(*) as trades,
                SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END) as wins,
                SUM(profit_loss) as total_profit,
                AVG(profit_loss_pct) as avg_profit_pct
            FROM performance
            WHERE 1=1
        """
        params: list[Any] = []

        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)

        query += " GROUP BY date ORDER BY date DESC LIMIT 30"

        cursor = self._conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def close(self) -> None:
        """Close database connection."""
        self._conn.close()

