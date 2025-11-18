"""Trade history storage for persistence across server restarts."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)


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

        -- AI 스캐너 결과 (코인별 점수/지표 기록)
        CREATE TABLE IF NOT EXISTS coin_scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id TEXT NOT NULL,
            scanned_at TEXT NOT NULL,
            market TEXT NOT NULL,
            score REAL NOT NULL,
            risk TEXT NOT NULL,
            trend TEXT,
            reason TEXT,
            current_price REAL,
            ma_5 REAL,
            ma_10 REAL,
            ma_20 REAL,
            volatility REAL,
            recent_change REAL,
            volume_ratio REAL,
            volume_24h REAL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_coin_scan_results_scan_id
            ON coin_scan_results (scan_id);

        CREATE INDEX IF NOT EXISTS idx_coin_scan_results_market_time
            ON coin_scan_results (market, scanned_at);

        -- AI 결정 로그 (매수/매도/HOLD 판단 기록)
        CREATE TABLE IF NOT EXISTS ai_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            decision_id TEXT NOT NULL,
            scan_id TEXT,
            decided_at TEXT NOT NULL,
            signal TEXT NOT NULL,
            market TEXT,
            confidence REAL,
            risk_level TEXT,
            reason TEXT,
            total_positions INTEGER,
            max_positions INTEGER,
            krw_balance REAL,
            total_balance REAL,
            candidates_json TEXT,
            alternatives_json TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_ai_decisions_time
            ON ai_decisions (decided_at);

        CREATE INDEX IF NOT EXISTS idx_ai_decisions_scan
            ON ai_decisions (scan_id);
        """
        with self._conn:
            self._conn.executescript(schema)

    # --- AI 스캐너/결정 결과 저장 유틸리티 ---

    def log_coin_scan_results(
        self,
        scan_id: str,
        scanned_at: str,
        coin_analyses: dict[str, dict[str, Any]],
    ) -> None:
        """여러 코인 스캔 결과를 coin_scan_results 테이블에 저장."""
        if not coin_analyses:
            return

        rows = []
        for market, analysis in coin_analyses.items():
            indicators = analysis.get("indicators", {}) or {}
            rows.append(
                (
                    scan_id,
                    scanned_at,
                    market,
                    float(analysis.get("score", 0.0)),
                    str(analysis.get("risk", "medium")),
                    analysis.get("trend"),
                    analysis.get("reason"),
                    indicators.get("current_price"),
                    indicators.get("ma_5"),
                    indicators.get("ma_10"),
                    indicators.get("ma_20"),
                    indicators.get("volatility"),
                    indicators.get("recent_change"),
                    indicators.get("volume_ratio"),
                    analysis.get("volume_24h"),
                )
            )

        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO coin_scan_results (
                    scan_id, scanned_at, market, score, risk, trend, reason,
                    current_price, ma_5, ma_10, ma_20,
                    volatility, recent_change, volume_ratio, volume_24h
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def log_ai_decision(
        self,
        decision_id: str,
        scan_id: str | None,
        decided_at: str,
        signal: str,
        market: str | None,
        confidence: float | None,
        risk_level: str | None,
        reason: str | None,
        total_positions: int | None,
        max_positions: int | None,
        krw_balance: float | None,
        total_balance: float | None,
        candidates: list[dict[str, Any]] | None = None,
        alternatives: list[dict[str, Any]] | None = None,
    ) -> int:
        """AI 매매 결정 결과를 ai_decisions 테이블에 저장."""
        decided_at_ts = decided_at or datetime.now(UTC).isoformat()
        with self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO ai_decisions (
                    decision_id, scan_id, decided_at,
                    signal, market, confidence, risk_level, reason,
                    total_positions, max_positions, krw_balance, total_balance,
                    candidates_json, alternatives_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    scan_id,
                    decided_at_ts,
                    signal,
                    market,
                    confidence,
                    risk_level,
                    reason,
                    total_positions,
                    max_positions,
                    krw_balance,
                    total_balance,
                    json.dumps(candidates or []),
                    json.dumps(alternatives or []),
                ),
            )
            return cursor.lastrowid

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
        """Get recent trades with PnL information from positions."""
        cursor = self._conn.execute(
            """
            SELECT 
                t.*,
                p.pnl,
                p.pnl_pct,
                p.entry_price,
                p.entry_amount,
                p.exit_price,
                p.exit_amount
            FROM trades t
            LEFT JOIN positions p ON (
                t.market = p.market 
                AND t.side = 'sell'
                AND p.status = 'closed'
                AND p.exit_time = (
                    SELECT exit_time 
                    FROM positions p2 
                    WHERE p2.market = t.market 
                    AND p2.status = 'closed'
                    AND p2.exit_time <= t.timestamp
                    ORDER BY p2.exit_time DESC 
                    LIMIT 1
                )
            )
            ORDER BY t.timestamp DESC
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

    def get_statistics(self, market: str | None = None, today_only: bool = False) -> dict[str, Any]:
        """Get trading statistics.
        
        Args:
            market: 특정 마켓 필터링 (None이면 모든 마켓)
            today_only: True면 오늘만, False면 누적 통계
        
        Note:
            manual 전략(사용자 직접 거래)은 통계에서 제외됩니다.
        """
        where_clause = "WHERE strategy != 'manual'"
        params = []
        if market:
            where_clause += " AND market = ?"
            params.append(market)
        
        # 오늘만 필터링
        today_filter = "DATE(timestamp) = DATE('now', 'localtime')"
        if today_only:
            where_clause += f" AND {today_filter}"

        # Total trades (manual 제외)
        cursor = self._conn.execute(
            f"SELECT COUNT(*) as count FROM trades {where_clause}",
            tuple(params),
        )
        total_trades = cursor.fetchone()["count"]

        # Closed positions 필터링 (manual 제외)
        pos_where = "WHERE status = 'closed' AND strategy != 'manual'"
        pos_params = []
        if market:
            pos_where += " AND market = ?"
            pos_params.append(market)
        if today_only:
            pos_where += " AND DATE(exit_time) = DATE('now', 'localtime')"
        
        # Closed positions (마이너스 손실 포함)
        cursor = self._conn.execute(
            f"SELECT COUNT(*) as count, COALESCE(SUM(pnl), 0) as total_pnl, COALESCE(AVG(pnl_pct), 0) as avg_pnl_pct "
            f"FROM positions {pos_where}",
            tuple(pos_params),
        )
        row = cursor.fetchone()
        closed_positions = row["count"] or 0
        # 마이너스 손실도 제대로 반영되도록 float 변환
        total_pnl = float(row["total_pnl"]) if row["total_pnl"] is not None else 0.0
        avg_pnl_pct = float(row["avg_pnl_pct"]) if row["avg_pnl_pct"] is not None else 0.0

        # Winning trades
        win_where = pos_where + " AND pnl > 0"
        cursor = self._conn.execute(
            f"SELECT COUNT(*) as count FROM positions {win_where}",
            tuple(pos_params),
        )
        winning_trades = cursor.fetchone()["count"] or 0

        win_rate = (winning_trades / closed_positions * 100) if closed_positions > 0 else 0.0
        
        # 상세 통계 (평균 수익/손실, 수익 팩터, MDD 등)
        cursor = self._conn.execute(
            f"SELECT AVG(CASE WHEN pnl > 0 THEN pnl ELSE NULL END) as avg_win, "
            f"AVG(CASE WHEN pnl < 0 THEN pnl ELSE NULL END) as avg_loss, "
            f"MAX(pnl) as max_profit, MIN(pnl) as max_loss "
            f"FROM positions {pos_where}",
            tuple(pos_params),
        )
        detail_row = cursor.fetchone()
        avg_win = float(detail_row["avg_win"]) if detail_row["avg_win"] is not None else 0.0
        avg_loss = float(detail_row["avg_loss"]) if detail_row["avg_loss"] is not None else 0.0
        max_profit = float(detail_row["max_profit"]) if detail_row["max_profit"] is not None else 0.0
        max_loss = float(detail_row["max_loss"]) if detail_row["max_loss"] is not None else 0.0
        
        # 수익 팩터
        profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0

        return {
            "total_trades": total_trades,
            "closed_positions": closed_positions,
            "winning_trades": winning_trades,
            "losing_trades": closed_positions - winning_trades,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "avg_pnl_pct": avg_pnl_pct,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "max_profit": max_profit,
            "max_loss": max_loss,
        }

    def clear_statistics(self, today_only: bool = False) -> str:
        """
        통계 데이터 초기화.
        
        Args:
            today_only: True면 오늘 거래만 삭제, False면 모든 거래 삭제
        
        Returns:
            초기화 결과 메시지
        """
        try:
            if today_only:
                # 오늘 거래만 삭제
                today_filter = "DATE(timestamp) = DATE('now', 'localtime')"
                
                # 오늘 거래 삭제
                self._conn.execute(
                    f"DELETE FROM trades WHERE {today_filter} AND strategy != 'manual'"
                )
                
                # 오늘 종료된 포지션 삭제
                self._conn.execute(
                    f"DELETE FROM positions WHERE DATE(exit_time) = DATE('now', 'localtime') AND strategy != 'manual'"
                )
                
                self._conn.commit()
                deleted_trades = self._conn.total_changes
                
                LOGGER.info(f"오늘 통계 초기화 완료: {deleted_trades}개 거래 삭제")
                return f"오늘 통계가 초기화되었습니다 ({deleted_trades}개 거래 삭제)"
            else:
                # 모든 거래 삭제 (manual 제외)
                self._conn.execute("DELETE FROM trades WHERE strategy != 'manual'")
                self._conn.execute("DELETE FROM positions WHERE strategy != 'manual'")
                self._conn.commit()
                deleted_trades = self._conn.total_changes
                
                LOGGER.info(f"누적 통계 초기화 완료: {deleted_trades}개 거래 삭제")
                return f"누적 통계가 초기화되었습니다 ({deleted_trades}개 거래 삭제)"
        except Exception as e:
            LOGGER.error(f"통계 초기화 실패: {e}")
            self._conn.rollback()
            raise

    def sync_external_trades(self, client: Any, days: int = 7) -> dict[str, Any]:
        """
        외부 거래 내역 동기화 (사용자가 직접 거래한 내용).
        
        Args:
            client: UpbitClient 인스턴스
            days: 동기화할 일수 (미사용, 최근 100개 주문 조회)
        
        Returns:
            동기화 결과 딕셔너리
        """
        try:
            LOGGER.info("외부 거래 내역 동기화 시작...")
            
            # 최근 완료된 주문 조회 (최대 100개)
            orders = client.get_orders(state="done", limit=100, order_by="desc")
            
            if not orders:
                LOGGER.info("동기화할 주문이 없습니다.")
                return {"success": True, "synced": 0, "errors": []}
            
            synced = 0
            skipped = 0
            errors = []
            
            for order in orders:
                try:
                    order_id = order.get("uuid")
                    if not order_id:
                        continue
                    
                    # order_id로 중복 체크
                    existing = self._conn.execute(
                        "SELECT id FROM trades WHERE order_id = ?", (order_id,)
                    ).fetchone()
                    
                    if existing:
                        skipped += 1
                        continue  # 이미 있으면 스킵
                    
                    # 주문 정보 추출
                    market = order.get("market")
                    side = order.get("side")  # "bid" or "ask"
                    order_type = order.get("ord_type")
                    state = order.get("state")
                    
                    if state != "done":
                        continue
                    
                    # 체결 정보 추출
                    executed_volume = float(order.get("executed_volume", 0))
                    avg_price = float(order.get("avg_price", 0))
                    
                    if executed_volume <= 0 or avg_price <= 0:
                        continue
                    
                    # 실제 체결 금액 계산
                    actual_amount = avg_price * executed_volume
                    
                    # 거래 내역으로 변환
                    signal = "BUY" if side == "bid" else "SELL"
                    trade_side = "buy" if side == "bid" else "sell"
                    
                    # 거래 저장
                    trade_id = self.save_trade(
                        market=market,
                        strategy="manual",  # 사용자 직접 거래
                        signal=signal,
                        side=trade_side,
                        price=avg_price,
                        volume=executed_volume,
                        amount=actual_amount,
                        order_id=order_id,
                        order_response=order,
                        dry_run=False,
                    )
                    
                    # 매수 시 포지션 생성, 매도 시 포지션 닫기
                    if side == "bid":
                        # 매수: 포지션 생성 (이미 있는 포지션은 스킵)
                        existing_positions = self.get_open_positions(market=market)
                        if not existing_positions:
                            # 새 포지션 생성
                            self.save_position(
                                market=market,
                                strategy="manual",
                                entry_price=avg_price,
                                entry_volume=executed_volume,
                                entry_amount=actual_amount,
                            )
                            LOGGER.debug(f"외부 매수 거래로 새 포지션 생성: {market}")
                        else:
                            # 이미 포지션이 있으면 추가 매수로 처리 (포지션 업데이트는 생략)
                            LOGGER.debug(f"외부 매수 거래: 이미 포지션이 있음 ({market}), 스킵")
                    elif side == "ask":
                        # 매도: 포지션 닫기
                        open_positions = self.get_open_positions(market=market)
                        if open_positions:
                            position_id = open_positions[0]["id"]
                            
                            # 포지션 닫기
                            self.close_position(
                                position_id=position_id,
                                exit_price=avg_price,
                                exit_volume=executed_volume,
                                exit_amount=actual_amount,
                            )
                            LOGGER.debug(f"외부 매도 거래로 포지션 닫기: {market}")
                    
                    synced += 1
                    LOGGER.debug(f"외부 거래 동기화: {market} {signal} {actual_amount:.0f}원")
                    
                except Exception as e:
                    error_msg = f"주문 {order.get('uuid', 'unknown')} 동기화 실패: {e}"
                    errors.append(error_msg)
                    LOGGER.warning(error_msg)
                    continue
            
            LOGGER.info(f"외부 거래 내역 동기화 완료: {synced}개 동기화, {skipped}개 스킵, {len(errors)}개 오류")
            return {"success": True, "synced": synced, "skipped": skipped, "errors": errors}
            
        except Exception as e:
            error_msg = f"외부 거래 내역 동기화 실패: {e}"
            LOGGER.error(error_msg, exc_info=True)
            return {"success": False, "error": error_msg}
    
    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

