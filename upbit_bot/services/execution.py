"""Execution loop for polling-based trading."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from threading import Event, Thread
from typing import Any

from upbit_bot.core import UpbitClient
from upbit_bot.data.trade_history import TradeHistoryStore
from upbit_bot.strategies import Candle, Strategy, StrategySignal
from upbit_bot.utils.notifications import Notifier

from .risk import PositionSizer, RiskManager

LOGGER = logging.getLogger(__name__)

# 포트폴리오 관리 설정
MAX_POSITIONS = 5  # 최대 동시 보유 가능한 코인 개수


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
        trade_history_store: TradeHistoryStore | None = None,
        order_amount_pct: float = 3.0,
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
        self.min_order_amount = max(min_order_amount, 0.0)  # 호환성 유지 (deprecated)
        self.order_amount_pct = max(0.1, min(order_amount_pct, 100.0))  # 0.1% ~ 100%
        self._stop_event: Event = Event()
        self._worker: Thread | None = None
        self.last_signal: StrategySignal | None = None
        self.last_order_info: dict | None = None
        self.last_run_at: datetime | None = None
        self.last_error: str | None = None
        self.last_ai_analysis: dict | None = None  # AI 분석 결과 저장
        self.trade_history_store = trade_history_store or TradeHistoryStore()
        self._load_positions()

    def _load_positions(self) -> None:
        """Load open positions from trade history."""
        try:
            open_positions = self.trade_history_store.get_open_positions(market=self.market)
            if open_positions:
                latest_position = open_positions[0]
                self.position_price = latest_position.get("entry_price")
                self.position_volume = latest_position.get("entry_volume")
                LOGGER.info(
                    "Loaded position: price=%.0f, volume=%.8f",
                    self.position_price or 0.0,
                    self.position_volume or 0.0,
                )
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to load positions: %s", exc)

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
        """
        동적 주문 금액 결정 (최소단위: 6,000 KRW).
        
        1. position_sizer가 있으면 사용
        2. 아니면 현재 KRW 잔액의 order_amount_pct 사용
        3. 둘 다 없으면 order_amount 사용 (후속 호환성)
        4. 최종 금액은 최소 6,000 KRW 이상 (손실 탈출을 위한 충분한 금액)
        
        계산:
        - 계산된 금액 ≥ 6,000 KRW: 설정된 퍼센트 사용
        - 계산된 금액 < 6,000 KRW: 6,000 KRW 사용
        """
        if self.position_sizer:
            stake = self.position_sizer.krw_stake()
        else:
            # 현재 KRW 잔액 조회
            try:
                account = self.client.get_accounts()
                krw_account = next((a for a in account if a["currency"] == "KRW"), None)
                krw_balance = float(krw_account["balance"]) if krw_account else 0.0
                # 보유 원화의 order_amount_pct 계산
                stake = krw_balance * (self.order_amount_pct / 100.0)
                LOGGER.debug(
                    f"Calculated order amount: {krw_balance} KRW * {self.order_amount_pct}% = {stake} KRW"
                )
            except Exception as e:
                # 조회 실패 시 order_amount 사용
                LOGGER.warning(f"Failed to get account balance: {e}")
                if self.order_amount is not None:
                    stake = self.order_amount
                else:
                    raise ValueError("Cannot determine order amount")
        
        MIN_ORDER_AMOUNT = 6000.0  # 최소 주문 금액: 6,000 KRW (손실 탈출용)
        final_amount = max(stake, MIN_ORDER_AMOUNT)
        
        if final_amount == MIN_ORDER_AMOUNT and stake < MIN_ORDER_AMOUNT:
            LOGGER.info(
                f"Order amount adjusted to minimum: {stake:.0f} KRW → {final_amount:.0f} KRW"
            )
        
        return final_amount
    
    def _try_escape_with_additional_buy(self, position_value: float, last_candle: Candle) -> bool:
        """
        저가 포지션 탈출 (추가 매수 후 즉시 매도).
        
        시나리오:
        - 현재 포지션 가치: 5,000원 이하
        - 매도 신호 발생
        
        탈출 프로세스:
        1. 5,000원을 시장가로 추가 매수
        2. 평단가 하락으로 수익성 개선
        3. 바로 전체 물량 매도
        
        목표:
        - 저가 물량 정리
        - 손실 최소화
        - 마진콜 방지
        
        반환값: 탈출 성공(True) / 실패(False)
        """
        MIN_SELL_AMOUNT = 5000.0
        
        if position_value > MIN_SELL_AMOUNT:
            return True  # 이미 매도 가능
        
        try:
            # 현재 KRW 잔액 확인
            account = self.client.get_accounts()
            krw_account = next((a for a in account if a["currency"] == "KRW"), None)
            krw_balance = float(krw_account["balance"]) if krw_account else 0.0
            
            # 5,000원 이상이 필요
            if krw_balance < MIN_SELL_AMOUNT:
                LOGGER.warning(
                    f"Cannot escape: position {position_value:.0f} KRW + balance {krw_balance:.0f} KRW < {MIN_SELL_AMOUNT:.0f} KRW"
                )
                return False
            
            # 5,000원으로 추가 매수
            buy_amount = MIN_SELL_AMOUNT
            buy_quantity = buy_amount / last_candle.close
            
            LOGGER.info(
                f"Attempting escape for {self.market}: buying {buy_quantity:.8f} @ {last_candle.close} = {buy_amount:.0f} KRW"
            )
            
            if self.dry_run:
                # 드라이런: 시뮬레이션
                new_total_quantity = (self.position_volume or 0.0) + buy_quantity
                new_total_cost = (self.position_volume or 0.0) * self.position_price + buy_amount
                new_avg_price = new_total_cost / new_total_quantity if new_total_quantity > 0 else 0
                
                self.position_volume = new_total_quantity
                self.position_price = new_avg_price
                
                LOGGER.info(
                    f"Dry-run escape: new avg price {new_avg_price:.0f} KRW, new position {new_total_quantity:.8f}"
                )
                
                # 거래 기록
                self.trade_history_store.save_trade(
                    market=self.market,
                    strategy=self.strategy.name,
                    signal="escape_buy",
                    side="buy",
                    price=last_candle.close,
                    volume=buy_quantity,
                    amount=buy_amount,
                )
                
                return True
            else:
                # 라이브: 실제 주문
                order = self.client.place_order(
                    market=self.market,
                    side="bid",
                    price=str(int(buy_amount)),
                    ord_type="market",
                )
                
                if order:
                    order_id = order.get("uuid")
                    # 주문 결과 반영
                    actual_volume = float(order.get("executed_volume", buy_quantity))
                    actual_cost = float(order.get("paid_fee", 0)) + buy_amount
                    
                    new_total_quantity = (self.position_volume or 0.0) + actual_volume
                    new_total_cost = (self.position_volume or 0.0) * self.position_price + actual_cost
                    new_avg_price = new_total_cost / new_total_quantity if new_total_quantity > 0 else 0
                    
                    self.position_volume = new_total_quantity
                    self.position_price = new_avg_price
                    
                    LOGGER.info(
                        f"Live escape executed: {order_id}, new avg price {new_avg_price:.0f}, new position {new_total_quantity:.8f}"
                    )
                    
                    # 거래 기록
                    self.trade_history_store.save_trade(
                        market=self.market,
                        strategy=self.strategy.name,
                        signal="escape_buy",
                        side="buy",
                        price=last_candle.close,
                        volume=actual_volume,
                        amount=actual_cost,
                    )
                    
                    return True
                else:
                    LOGGER.error("Escape buy order failed")
                    return False
                    
        except Exception as e:
            LOGGER.error(f"Escape attempt failed: {e}")
            return False
    
    def _can_sell(self, position_value: float, last_candle: Candle | None = None) -> bool:
        """
        매도 처리 로직.
        
        매도 신호 들어올 때:
        1. 포지션 가치 > 5,000원: 바로 매도 ✅
        2. 포지션 가치 ≤ 5,000원: 
           - 5,000원 추가 매수
           - 평단가 하락
           - 즉시 매도 ✅
        
        탈출 불가능한 경우만 False 반환
        """
        MIN_SELL_AMOUNT = 5000.0
        
        if position_value > MIN_SELL_AMOUNT:
            # 포지션 가치 충분 → 바로 매도
            return True
        
        # 5,000원 이하 → 탈출 시도 (추가 매수 후 매도)
        if last_candle:
            if self._try_escape_with_additional_buy(position_value, last_candle):
                LOGGER.info("Escape with additional buy successful, proceeding with sell")
                return True
        
        LOGGER.warning(
            f"Cannot sell: position value {position_value:.0f} KRW ≤ {MIN_SELL_AMOUNT:.0f} KRW, "
            f"escape failed (insufficient balance or data)"
        )
        return False

    def _notify(self, message: str, **kwargs) -> None:
        for notifier in self.notifiers:
            notifier.send(message, **kwargs)

    def _execute_signal(self, signal: StrategySignal, candles: list[Candle]) -> dict | None:
        self.last_order_info = None
        if signal is StrategySignal.HOLD:
            LOGGER.debug("No action taken for market %s (HOLD signal)", self.market)
            return None

        last_candle = candles[-1]
        side = "bid" if signal is StrategySignal.BUY else "ask"
        LOGGER.info(
            "🔥 EXECUTING SIGNAL: %s -> %s for %s (price %.0f)",
            self.strategy.name,
            signal.value,
            self.market,
            last_candle.close,
        )

        if signal is StrategySignal.BUY:
            if self.position_price is not None:
                LOGGER.warning("⚠️ BUY SIGNAL IGNORED: Position already open for %s (price: %.0f, volume: %.6f)", 
                              self.market, self.position_price, self.position_volume or 0.0)
                return None
            
            # 포트폴리오 체크: 최대 5개 포지션
            if not self.can_open_new_position():
                # 최대 개수 모두 찼으면 가장 나쁜 포지션 청산
                LOGGER.warning(f"⚠️ BUY SIGNAL BLOCKED: Portfolio full ({MAX_POSITIONS} positions). Attempting to liquidate worst position...")
                liquidate_result = self.liquidate_worst_position()
                if not liquidate_result.get("success"):
                    LOGGER.error(f"❌ BUY SIGNAL CANCELLED: Failed to liquidate worst position: {liquidate_result.get('error')}")
                    return None
                # 청산 후 새로운 포지션 매수
                LOGGER.info(f"✅ Portfolio space created, proceeding with BUY signal")
            
            if self.risk_manager and not self.risk_manager.can_open_position(self.market):
                LOGGER.warning("⚠️ BUY SIGNAL BLOCKED: Risk manager rejected opening position for %s", self.market)
                return None
            stake = self._determine_order_amount()
            est_volume = stake / last_candle.close if last_candle.close else 0.0
            
            LOGGER.info("💰 BUY SIGNAL PROCESSING: market=%s, stake=%.0f KRW, estimated_volume=%.6f, price=%.0f", 
                       self.market, stake, est_volume, last_candle.close)
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
                
                # Save trade history
                try:
                    balance_before = self.position_sizer.balance_fetcher() if self.position_sizer else None
                    position_id = self.trade_history_store.save_position(
                        market=self.market,
                        strategy=self.strategy.name,
                        entry_price=last_candle.close,
                        entry_volume=est_volume,
                        entry_amount=stake,
                    )
                    self.trade_history_store.save_trade(
                        market=self.market,
                        strategy=self.strategy.name,
                        signal=signal.value,
                        side="buy",
                        price=last_candle.close,
                        volume=est_volume,
                        amount=stake,
                        dry_run=self.dry_run,
                        balance_before=balance_before,
                        balance_after=balance_before - stake if balance_before else None,
                    )
                    LOGGER.debug("Trade history saved: position_id=%s", position_id)
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("Failed to save trade history: %s", exc)
                
                self._notify(
                    "Dry-run BUY executed",
                    market=self.market,
                    stake=stake,
                    price=last_candle.close,
                )
                return info

            LOGGER.info("📤 Placing BUY order: market=%s, side=%s, amount=%.0f KRW", self.market, side, stake)
            response = self.client.place_order(
                self.market,
                side=side,
                ord_type="price",
                price=f"{stake:.0f}",
            )
            LOGGER.info("✅ BUY order placed successfully: %s", response)
            
            # 실제 주문 응답에서 체결 정보 확인
            actual_entry_amount = stake
            actual_entry_price = last_candle.close
            actual_entry_volume = est_volume
            
            # 업비트 주문 응답에서 실제 체결 정보 추출
            if isinstance(response, dict):
                order_uuid = response.get("uuid")
                # 업비트 API 응답 구조: avg_price * executed_volume = 실제 체결 금액
                executed_volume = response.get("executed_volume")
                avg_price = response.get("avg_price")  # 평균 체결가
                
                # 주문 조회 API로 실제 체결 정보 확인 (응답에 체결 정보가 없을 때)
                if (not executed_volume or not avg_price or float(executed_volume) == 0) and order_uuid:
                    try:
                        import time
                        # 주문 체결 대기 (최대 2초)
                        for _ in range(4):
                            time.sleep(0.5)
                            order_info = self.client.get_order(uuid=order_uuid)
                            if isinstance(order_info, dict):
                                executed_volume = order_info.get("executed_volume")
                                avg_price = order_info.get("avg_price")
                                state = order_info.get("state")
                                
                                if state == "done" and executed_volume and avg_price and float(executed_volume) > 0:
                                    LOGGER.info(f"✅ BUY: Got executed info from order query - price: {float(avg_price):.0f}, volume: {float(executed_volume):.6f}")
                                    break
                    except Exception as e:
                        LOGGER.warning(f"Failed to query order status: {e}")
                
                if executed_volume and avg_price and float(executed_volume) > 0:
                    # 실제 체결 정보 사용
                    actual_entry_volume = float(executed_volume)
                    actual_entry_price = float(avg_price)
                    actual_entry_amount = actual_entry_price * actual_entry_volume
                    LOGGER.info(f"✅ BUY: Using actual executed data - price: {actual_entry_price:.0f}, volume: {actual_entry_volume:.6f}, amount: {actual_entry_amount:.0f}원 (expected: {stake:.0f}원)")
                else:
                    # 주문 응답에 체결 정보가 없거나 아직 체결 안됨 (예상 금액 사용)
                    LOGGER.warning(f"⚠️ BUY: No executed info available (executed_volume: {executed_volume}, avg_price: {avg_price}), using estimated amount: {stake:.0f}원")
            
            self.position_price = actual_entry_price
            self.position_volume = actual_entry_volume
            if self.risk_manager:
                balance = self.position_sizer.balance_fetcher() if self.position_sizer else stake
                stake_pct = (actual_entry_amount / balance * 100) if balance else 0.0
                self.risk_manager.register_entry(self.market, stake_pct=stake_pct)
            
            # Save trade history
            try:
                balance_before = self.position_sizer.balance_fetcher() if self.position_sizer else None
                order_id = response.get("uuid") if isinstance(response, dict) else None
                position_id = self.trade_history_store.save_position(
                    market=self.market,
                    strategy=self.strategy.name,
                    entry_price=actual_entry_price,
                    entry_volume=actual_entry_volume,
                    entry_amount=actual_entry_amount,
                )
                self.trade_history_store.save_trade(
                    market=self.market,
                    strategy=self.strategy.name,
                    signal=signal.value,
                    side="buy",
                    price=actual_entry_price,
                    volume=actual_entry_volume,
                    amount=actual_entry_amount,
                    order_id=order_id,
                    order_response=response,
                    dry_run=self.dry_run,
                    balance_before=balance_before,
                    balance_after=balance_before - actual_entry_amount if balance_before else None,
                )
                LOGGER.debug("Trade history saved: position_id=%s", position_id)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Failed to save trade history: %s", exc)
            
            self.last_order_info = response
            self._notify(
                "Live BUY placed",
                market=self.market,
                stake=stake,
                response=response,
            )
            return response

        if self.position_price is None:
            LOGGER.warning("⚠️ SELL SIGNAL IGNORED: No open position for %s", self.market)
            return None

        # 매도 처리 (신호 발생 시 무조건 매도, 필요시 추가 매수)
        sell_amount = (self.position_volume or 0.0) * last_candle.close
        if not self._can_sell(sell_amount, last_candle):
            LOGGER.warning(
                "⚠️ SELL signal failed: position value %.0f KRW, unable to execute (insufficient balance or data)",
                sell_amount
            )
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
            
            # Save trade history and close position
            try:
                open_positions = self.trade_history_store.get_open_positions(market=self.market)
                if open_positions:
                    position_id = open_positions[0]["id"]
                    sell_amount = (self.position_volume or 0.0) * last_candle.close
                    self.trade_history_store.close_position(
                        position_id=position_id,
                        exit_price=last_candle.close,
                        exit_volume=self.position_volume or 0.0,
                        exit_amount=sell_amount,
                    )
                    balance_before = self.position_sizer.balance_fetcher() if self.position_sizer else None
                    self.trade_history_store.save_trade(
                        market=self.market,
                        strategy=self.strategy.name,
                        signal=signal.value,
                        side="sell",
                        price=last_candle.close,
                        volume=self.position_volume or 0.0,
                        amount=sell_amount,
                        dry_run=self.dry_run,
                        balance_before=balance_before,
                        balance_after=balance_before + sell_amount if balance_before else None,
                    )
                    LOGGER.debug("Trade history saved: position_id=%s closed", position_id)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Failed to save trade history: %s", exc)
            
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
        LOGGER.info("📤 Placing SELL order: market=%s, side=%s, volume=%.6f", self.market, side, volume)
        response = self.client.place_order(
            self.market,
            side=side,
            ord_type="market",
            volume=f"{volume}",
        )
        LOGGER.info("✅ SELL order placed successfully: %s", response)
        if self.risk_manager:
            self.risk_manager.register_exit(self.market, pnl_pct=pnl_pct)
        
        # Save trade history and close position
        try:
            open_positions = self.trade_history_store.get_open_positions(market=self.market)
            if open_positions:
                position_id = open_positions[0]["id"]
                
                # 실제 주문 응답에서 체결 금액 확인
                actual_exit_amount = sell_amount
                actual_exit_price = last_candle.close
                actual_exit_volume = self.position_volume or 0.0
                
                # 업비트 주문 응답에서 실제 체결 정보 추출
                if isinstance(response, dict):
                    order_uuid = response.get("uuid")
                    # 업비트 API 응답 구조: avg_price * executed_volume = 실제 체결 금액
                    executed_volume = response.get("executed_volume")
                    avg_price = response.get("avg_price")  # 평균 체결가
                    
                    # 주문 조회 API로 실제 체결 정보 확인 (응답에 체결 정보가 없을 때)
                    if (not executed_volume or not avg_price or float(executed_volume) == 0) and order_uuid:
                        try:
                            import time
                            # 주문 체결 대기 (최대 2초)
                            for _ in range(4):
                                time.sleep(0.5)
                                order_info = self.client.get_order(uuid=order_uuid)
                                if isinstance(order_info, dict):
                                    executed_volume = order_info.get("executed_volume")
                                    avg_price = order_info.get("avg_price")
                                    state = order_info.get("state")
                                    
                                    if state == "done" and executed_volume and avg_price and float(executed_volume) > 0:
                                        LOGGER.info(f"✅ SELL: Got executed info from order query - price: {float(avg_price):.0f}, volume: {float(executed_volume):.6f}")
                                        break
                        except Exception as e:
                            LOGGER.warning(f"Failed to query order status: {e}")
                    
                    if executed_volume and avg_price and float(executed_volume) > 0:
                        # 실제 체결 정보 사용
                        actual_exit_volume = float(executed_volume)
                        actual_exit_price = float(avg_price)
                        actual_exit_amount = actual_exit_price * actual_exit_volume
                        LOGGER.info(f"✅ SELL: Using actual executed data - price: {actual_exit_price:.0f}, volume: {actual_exit_volume:.6f}, amount: {actual_exit_amount:.0f}원 (expected: {sell_amount:.0f}원)")
                    else:
                        # 주문 응답에 체결 정보가 없으면 예상 금액 사용
                        LOGGER.warning(f"⚠️ SELL: No executed info available (executed_volume: {executed_volume}, avg_price: {avg_price}), using estimated amount: {sell_amount:.0f}원")
                        LOGGER.debug(f"Full response: {response}")
                
                self.trade_history_store.close_position(
                    position_id=position_id,
                    exit_price=actual_exit_price,
                    exit_volume=actual_exit_volume,
                    exit_amount=actual_exit_amount,
                )
                balance_before = self.position_sizer.balance_fetcher() if self.position_sizer else None
                order_id = response.get("uuid") if isinstance(response, dict) else None
                self.trade_history_store.save_trade(
                    market=self.market,
                    strategy=self.strategy.name,
                    signal=signal.value,
                    side="sell",
                    price=actual_exit_price,
                    volume=actual_exit_volume,
                    amount=actual_exit_amount,
                    order_id=order_id,
                    order_response=response,
                    dry_run=self.dry_run,
                    balance_before=balance_before,
                    balance_after=balance_before + actual_exit_amount if balance_before else None,
                )
                LOGGER.debug("Trade history saved: position_id=%s closed", position_id)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to save trade history: %s", exc)
        
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

    def _analyze_multiple_markets(self) -> tuple[str | None, StrategySignal, list[Candle]]:
        """
        AI 전략일 때 여러 코인을 분석하여 최적의 코인 선택 (이중 Ollama 아키텍처).
        
        Returns:
            (선택된 market, signal, candles)
        """
        # AI 전략인지 확인 (기본 AI 전략 또는 고위험 AI 전략)
        ai_strategies = ["ai_market_analyzer", "ai_market_analyzer_high_risk"]
        if self.strategy.name not in ai_strategies:
            # AI 전략이 아니면 기존 방식
            candles = self._fetch_candles()
            signal = self.strategy.on_candles(candles)
            return self.market, signal, candles
        
        # AI 전략: 이중 Ollama 엔진 사용
        try:
            from .dual_ollama_engine import DualOllamaEngine
            
            # 이중 Ollama 엔진 초기화 (전략에 이미 있으면 재사용)
            if hasattr(self.strategy, '_get_dual_engine'):
                dual_engine = self.strategy._get_dual_engine()
            elif hasattr(self.strategy, 'dual_engine') and self.strategy.dual_engine:
                dual_engine = self.strategy.dual_engine
            else:
                is_high_risk = self.strategy.name == "ai_market_analyzer_high_risk"
                confidence_threshold = (
                    getattr(self.strategy, 'confidence_threshold', 0.4)
                    if is_high_risk
                    else 0.6
                )
                # 분산 모드 확인
                import os
                use_distributed = os.getenv("USE_DISTRIBUTED_SCANNER", "false").lower() == "true"
                remote_scanner_url = os.getenv("SCANNER_API_URL")
                
                dual_engine = DualOllamaEngine(
                    confidence_threshold=confidence_threshold,
                    high_risk=is_high_risk,
                    use_distributed=use_distributed,
                    remote_scanner_url=remote_scanner_url,
                )
            
            # 거래량 상위 30개 코인 가져오기 (하이브리드 방식: 상위 30개만 스캔)
            markets = self.client.get_top_volume_markets(limit=30)
            LOGGER.info(f"거래량 상위 30개 코인 선택: {len(markets)}개")
            
            # 이미 포지션이 있는 코인은 제외
            portfolio = self.get_portfolio_status()
            open_markets = {pos.get("market") for pos in portfolio.get("open_positions", [])}
            markets_to_analyze = [m for m in markets if m not in open_markets]
            
            if not markets_to_analyze:
                LOGGER.info("분석할 코인이 없음 (모두 포지션 보유 중)")
                candles = self._fetch_candles()
                signal = self.strategy.on_candles(candles)
                return self.market, signal, candles
            
            LOGGER.info(f"이중 Ollama 분석 시작: {len(markets_to_analyze)}개 코인")
            
            # 모든 코인의 캔들 데이터 수집
            markets_data: dict[str, list[Candle]] = {}
            for market in markets_to_analyze:
                try:
                    raw = self.client.get_candles(market, unit=self.candle_unit, count=min(self.candle_count, 20))
                    if not raw:
                        continue
                    
                    candles_list = [
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
                    
                    if len(candles_list) >= 5:
                        markets_data[market] = candles_list
                except Exception as e:
                    LOGGER.warning(f"코인 {market} 캔들 데이터 수집 실패: {e}")
                    continue
            
            if not markets_data:
                LOGGER.warning("유효한 캔들 데이터가 없음")
                candles = self._fetch_candles()
                signal = self.strategy.on_candles(candles)
                return self.market, signal, candles
            
            # 시장 상황 정보 수집
            market_context = {
                "total_balance": portfolio.get("total_balance", 0),
                "krw_balance": portfolio.get("krw_balance", 0),
                "max_positions": MAX_POSITIONS,
                "current_positions": len(portfolio.get("open_positions", [])),
                "risk_level": "medium" if self.strategy.name == "ai_market_analyzer" else "high",
                "market_trend": "unknown",
                "min_order_amount": self.min_order_amount,
            }
            
            # markets_data 콜백 함수 생성 (백그라운드 스캔용)
            def get_markets_data() -> dict[str, list[Candle]]:
                """markets_data를 반환하는 콜백 함수"""
                try:
                    # 거래량 상위 30개 코인 가져오기
                    top_markets = self.client.get_top_volume_markets(limit=30)
                    markets_data_result: dict[str, list[Candle]] = {}
                    
                    for market in top_markets[:30]:  # 최대 30개만
                        try:
                            raw = self.client.get_candles(market, unit=self.candle_unit, count=min(self.candle_count, 20))
                            if not raw:
                                continue
                            
                            candles_list = [
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
                            
                            if len(candles_list) >= 5:
                                markets_data_result[market] = candles_list
                        except Exception as e:
                            LOGGER.debug(f"백그라운드 스캔: {market} 캔들 데이터 수집 실패: {e}")
                            continue
                    
                    return markets_data_result
                except Exception as e:
                    LOGGER.warning(f"백그라운드 스캔 markets_data 콜백 오류: {e}")
                    return {}
            
            # Ollama 1 백그라운드 스캔 시작 (최초 1회만)
            if not hasattr(dual_engine.scanner, '_background_started'):
                try:
                    dual_engine.scanner.start_background_scanning(
                        get_markets_data_callback=get_markets_data,
                        interval_seconds=300,  # 5분마다 스캔
                    )
                    dual_engine.scanner._background_started = True
                    LOGGER.info("Ollama 1 백그라운드 스캔 시작됨 (서버 시작/종료와 별개)")
                except Exception as e:
                    LOGGER.warning(f"Ollama 1 백그라운드 스캔 시작 실패: {e}")
            
            # 이중 Ollama 엔진으로 분석
            signal, selected_market, confidence, analysis_data = dual_engine.analyze_markets(
                markets_data=markets_data,
                current_portfolio=portfolio,
                market_context=market_context,
            )

            # AI 스캐너/결정 결과를 DB에 기록 (가능한 경우)
            try:
                if self.trade_history_store:
                    # 공통 ID 생성
                    scan_id = analysis_data.get("scan_id") or str(uuid.uuid4())
                    decision_id = analysis_data.get("decision", {}).get("decision_id") or str(
                        uuid.uuid4()
                    )
                    now_ts = datetime.now(UTC).isoformat()

                    # 1) 코인 스캔 결과 저장 (가능한 경우)
                    coin_analyses = analysis_data.get("coin_analyses") or analysis_data.get(
                        "scan_results"
                    )
                    if isinstance(coin_analyses, dict) and coin_analyses:
                        self.trade_history_store.log_coin_scan_results(
                            scan_id=scan_id,
                            scanned_at=now_ts,
                            coin_analyses=coin_analyses,
                        )

                    # 2) 매매 결정 결과 저장
                    decision = analysis_data.get("decision") or {}
                    signal_str = decision.get("signal") or signal.value if signal else "HOLD"
                    decision_market = decision.get("market") or selected_market
                    risk_info = decision.get("risk") or decision.get("risk_assessment") or {}
                    risk_level = (
                        risk_info.get("level")
                        if isinstance(risk_info, dict)
                        else risk_info or None
                    )
                    reason = decision.get("reason")
                    candidates = decision.get("candidates") or decision.get(
                        "alternative_options"
                    )

                    self.trade_history_store.log_ai_decision(
                        decision_id=decision_id,
                        scan_id=scan_id,
                        decided_at=now_ts,
                        signal=signal_str,
                        market=decision_market,
                        confidence=decision.get("confidence", confidence),
                        risk_level=risk_level,
                        reason=reason,
                        total_positions=portfolio.get("total_positions"),
                        max_positions=market_context.get("max_positions"),
                        krw_balance=market_context.get("krw_balance"),
                        total_balance=market_context.get("total_balance"),
                        candidates=candidates if isinstance(candidates, list) else [],
                        alternatives=decision.get("alternatives")
                        if isinstance(decision.get("alternatives"), list)
                        else decision.get("alternative_options")
                        if isinstance(decision.get("alternative_options"), list)
                        else [],
                    )
            except Exception as db_exc:  # noqa: BLE001
                LOGGER.warning(f"AI 기록 저장 중 오류 발생: {db_exc}")
            
            # 분석 결과 저장
            if hasattr(self.strategy, 'last_analysis'):
                self.strategy.last_analysis = analysis_data
            
            # 선택된 코인으로 market 업데이트
            if selected_market:
                self.market = selected_market
                candles = markets_data.get(selected_market, self._fetch_candles())
                
                LOGGER.info(
                    f"✅ 이중 Ollama 분석 완료: {selected_market} {signal.value} "
                    f"(신뢰도: {confidence:.2%})"
                )
                return selected_market, signal, candles
            else:
                # 신호가 없으면 기존 market 유지
                LOGGER.info("매매 신호 없음, 기본 코인 유지")
                candles = self._fetch_candles()
                return self.market, signal, candles
                
        except Exception as e:
            LOGGER.error(f"이중 Ollama 분석 실패: {e}, 기본 코인 사용", exc_info=True)
            candles = self._fetch_candles()
            signal = self.strategy.on_candles(candles)
            return self.market, signal, candles
    
    def run_once(self) -> dict | None:
        # AI 전략이면 여러 코인 분석, 아니면 기존 방식
        selected_market, signal, candles = self._analyze_multiple_markets()
        
        # 시그널 발생 여부 명확히 로깅
        if signal is StrategySignal.BUY:
            LOGGER.info("🟢 BUY SIGNAL DETECTED: Strategy %s -> BUY for %s", self.strategy.name, selected_market)
        elif signal is StrategySignal.SELL:
            LOGGER.info("🔴 SELL SIGNAL DETECTED: Strategy %s -> SELL for %s", self.strategy.name, selected_market)
        else:
            LOGGER.info("⚪ HOLD SIGNAL: Strategy %s -> HOLD for %s", self.strategy.name, selected_market)
        
        # AI 전략인 경우 분석 결과 저장 (기본 AI 전략 또는 고위험 AI 전략)
        ai_strategies = ["ai_market_analyzer", "ai_market_analyzer_high_risk"]
        if self.strategy.name in ai_strategies:
            # AI 분석 결과가 있으면 저장
            if hasattr(self.strategy, 'last_analysis') and self.strategy.last_analysis:
                self.last_ai_analysis = self.strategy.last_analysis.copy()
                # 선택된 market 정보 추가
                self.last_ai_analysis['selected_market'] = selected_market
                # 타임스탬프 추가
                self.last_ai_analysis['timestamp'] = datetime.now(UTC).isoformat()
                
                # signal을 문자열로 변환 (StrategySignal enum -> string)
                signal_obj = self.last_ai_analysis.get('signal')
                if signal_obj is not None:
                    if hasattr(signal_obj, 'value'):
                        self.last_ai_analysis['signal'] = signal_obj.value
                    elif hasattr(signal_obj, 'name'):
                        self.last_ai_analysis['signal'] = signal_obj.name
                    else:
                        self.last_ai_analysis['signal'] = str(signal_obj)
                
                LOGGER.info(f"AI analysis saved: market={selected_market}, signal={self.last_ai_analysis.get('signal')}, confidence={self.last_ai_analysis.get('confidence', 0):.2%}")
            else:
                # AI 분석 결과가 없으면 빈 결과 저장 (콘솔에 표시하기 위해)
                self.last_ai_analysis = {
                    "market_data": {},
                    "signal": signal.value if signal else "HOLD",
                    "confidence": 0.0,
                    "selected_market": selected_market,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "status": "no_analysis"
                }
                LOGGER.warning("AI analysis result not available, but strategy is ai_market_analyzer")
        
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

    def force_exit_all(self) -> dict[str, Any]:
        """
        강제 탈출: 모든 거래 가능한 코인을 시장가로 매도.
        
        작동:
        1. 현재 보유한 모든 코인 조회
        2. 거래 가능한 코인만 필터링
        3. 각 코인을 시장가 매도
        4. 결과 반환
        """
        results = {
            "status": "force_exit",
            "executed_at": datetime.now(UTC).isoformat(),
            "sells": [],
            "errors": [],
        }
        
        try:
            # 현재 계정 정보 조회
            accounts = self.client.get_accounts()
            
            # KRW 제외하고 0 이상의 잔액 있는 코인만 필터링
            coins_to_sell = [
                a for a in accounts
                if a["currency"] != "KRW" and float(a.get("balance", 0)) > 0
            ]
            
            if not coins_to_sell:
                results["message"] = "No coins to sell"
                LOGGER.info("Force exit: No coins to sell")
                return results
            
            # 각 코인 매도
            for coin_account in coins_to_sell:
                currency = coin_account["currency"]
                balance = float(coin_account["balance"])
                market = f"KRW-{currency}"
                
                try:
                    # 현재 시세 조회
                    ticker = self.client.get_ticker(market)
                    if not ticker:
                        results["errors"].append(f"No ticker for {market}")
                        continue
                    
                    current_price = float(ticker.get("trade_price", 0))
                    sell_amount = balance * current_price
                    
                    # 최소 금액 체크
                    if sell_amount < 5000:
                        results["errors"].append(
                            f"{market}: 판매금액 {sell_amount:.0f}원 < 5,000원 (스킵)"
                        )
                        continue
                    
                    # 시장가 매도
                    order = self.client.place_order(
                        market=market,
                        side="ask",  # 매도
                        volume=str(balance),
                        ord_type="market",
                    )
                    
                    # 거래 내역 기록
                    if self.trade_history_store:
                        try:
                            # 기존 포지션 찾기 및 닫기
                            positions = self.trade_history_store.get_open_positions(market=market)
                            for pos in positions:
                                # 포지션 닫기 (close_position이 자동으로 pnl 계산)
                                self.trade_history_store.close_position(
                                    position_id=pos["id"],
                                    exit_price=current_price,
                                    exit_volume=balance,
                                    exit_amount=sell_amount,
                                )
                            
                            # 거래 기록 저장
                            self.trade_history_store.save_trade(
                                market=market,
                                strategy="force_exit",
                                signal="FORCE_SELL",
                                side="sell",
                                price=current_price,
                                volume=balance,
                                amount=sell_amount,
                                order_id=order.get("uuid"),
                                order_response=order,
                                dry_run=self.dry_run,
                            )
                            LOGGER.info(f"Force exit trade recorded: {market}")
                        except Exception as e:
                            LOGGER.error(f"Failed to record force exit trade for {market}: {e}", exc_info=True)
                    
                    results["sells"].append({
                        "market": market,
                        "balance": balance,
                        "price": current_price,
                        "amount": sell_amount,
                        "order_id": order.get("uuid"),
                        "status": "success",
                    })
                    
                    LOGGER.info(
                        f"Force exit SELL: {market} {balance} @ {current_price} = {sell_amount:.0f}원"
                    )
                    
                except Exception as e:
                    results["errors"].append(f"{market}: {str(e)}")
                    LOGGER.error(f"Force exit error for {market}: {e}")
            
            results["message"] = f"강제 탈출 완료: {len(results['sells'])}개 코인 매도"
            
        except Exception as e:
            results["status"] = "error"
            results["error"] = str(e)
            LOGGER.error(f"Force exit failed: {e}")
        
        return results
    
    def get_portfolio_status(self) -> dict[str, Any]:
        """
        현재 포트폴리오 상태 조회.
        
        웹페이지 자산현황 기준: 실제 계정 잔액이 있는 것만 포지션으로 인정
        
        반환:
        - total_positions: 열린 포지션 개수 (실제 잔액 기준)
        - open_positions: 열린 포지션 목록 (수익률 순 정렬, 실제 잔액 기준)
        - worst_position: 가장 낮은 수익률 포지션
        """
        try:
            # 실제 계정 잔액 확인
            accounts = self.client.get_accounts()
            actual_balances = {}
            for account in accounts:
                currency = account.get("currency", "")
                if currency != "KRW":
                    balance = float(account.get("balance", 0))
                    if balance > 0:
                        actual_balances[currency] = balance
            
            # DB에서 열린 포지션 조회
            positions = self.trade_history_store.get_open_positions()
            
            if not positions:
                return {
                    "total_positions": 0,
                    "open_positions": [],
                    "worst_position": None,
                }
            
            # 각 포지션의 현재가 및 수익률 계산 (실제 잔액이 있는 것만)
            positions_with_pnl = []
            cleaned_positions = []  # 실제 잔액 없는 포지션 목록
            
            for pos in positions:
                market = pos.get("market")
                currency = market.replace("KRW-", "")
                entry_price = float(pos.get("entry_price", 0))
                entry_volume = float(pos.get("entry_volume", 0))
                
                # 실제 계정 잔액 확인
                actual_balance = actual_balances.get(currency, 0.0)
                
                # 실제 잔액이 없으면 포지션 정리 (웹페이지 자산현황에 없으면 없는 것)
                if actual_balance <= 0:
                    try:
                        position_id = pos.get("id")
                        entry_amount = float(pos.get("entry_amount", 0))
                        LOGGER.info(f"Cleaning up position {position_id} for {market}: no actual balance (entry_volume: {entry_volume}, entry_amount: {entry_amount:.0f}원)")
                        # 포지션을 강제로 종료 (실제로 손실 처리 - 매수 금액만큼 손실)
                        # exit_amount는 0이지만 entry_amount만큼 손실로 기록
                        self.trade_history_store.close_position(
                            position_id=position_id,
                            exit_price=entry_price,  # 매수가 기준
                            exit_volume=0.0,
                            exit_amount=0.0,  # 실제로 받은 금액은 0
                        )
                        # PnL은 close_position에서 자동 계산됨: exit_amount(0) - entry_amount = -entry_amount
                        cleaned_positions.append(f"{market} (손실: {entry_amount:.0f}원)")
                    except Exception as e:
                        LOGGER.warning(f"Failed to clean up position for {market}: {e}")
                    continue
                
                try:
                    # 현재가 조회
                    ticker = self.client.get_ticker(market)
                    if not ticker:
                        continue
                    
                    current_price = float(ticker.get("trade_price", 0))
                    # 실제 잔액 기준으로 수익률 계산
                    actual_value = actual_balance * current_price
                    pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                    
                    positions_with_pnl.append({
                        **pos,
                        "current_price": current_price,
                        "pnl_pct": pnl_pct,
                        "current_value": actual_value,
                        "actual_balance": actual_balance,  # 실제 잔액 추가
                        "entry_volume": entry_volume,  # DB 기록
                    })
                    
                except Exception as e:
                    LOGGER.warning(f"Failed to get price for {market}: {e}")
                    continue
            
            if cleaned_positions:
                LOGGER.info(f"Cleaned up {len(cleaned_positions)} positions with no actual balance: {cleaned_positions}")
            
            # 수익률 기준 정렬 (가장 낮은 것부터)
            positions_with_pnl.sort(key=lambda x: x.get("pnl_pct", 0))
            
            return {
                "total_positions": len(positions_with_pnl),
                "open_positions": positions_with_pnl,
                "worst_position": positions_with_pnl[0] if positions_with_pnl else None,
            }
            
        except Exception as e:
            LOGGER.error(f"Failed to get portfolio status: {e}")
            return {
                "total_positions": 0,
                "open_positions": [],
                "worst_position": None,
                "error": str(e),
            }
    
    def can_open_new_position(self) -> bool:
        """
        새로운 포지션 오픈 가능 여부 판단.
        
        최대 MAX_POSITIONS개 포지션만 동시 보유 가능
        """
        portfolio = self.get_portfolio_status()
        return portfolio.get("total_positions", 0) < MAX_POSITIONS
    
    def liquidate_worst_position(self) -> dict[str, Any]:
        """
        가장 낮은 수익률 포지션 청산.
        
        작동:
        1. 포트폴리오 상태 확인
        2. 가장 낮은 수익률 포지션 찾기
        3. 실제 계정 잔액 확인
        4. 해당 포지션 시장가 매도
        5. 결과 반환
        """
        result = {
            "status": "liquidate_worst",
            "executed_at": datetime.now(UTC).isoformat(),
            "success": False,
        }
        
        try:
            portfolio = self.get_portfolio_status()
            worst_pos = portfolio.get("worst_position")
            
            if not worst_pos:
                result["error"] = "No open positions"
                return result
            
            market = worst_pos.get("market")
            entry_volume = float(worst_pos.get("entry_volume", 0))
            current_price = float(worst_pos.get("current_price", 0))
            pnl_pct = worst_pos.get("pnl_pct", 0)
            
            # 실제 계정 잔액 확인
            currency = market.replace("KRW-", "")
            actual_balance = 0.0
            try:
                accounts = self.client.get_accounts()
                for account in accounts:
                    if account.get("currency") == currency:
                        actual_balance = float(account.get("balance", 0))
                        break
            except Exception as e:
                LOGGER.warning(f"Failed to get actual balance for {market}: {e}")
            
            # 실제 잔액과 DB 기록 중 더 작은 값 사용
            sell_volume = min(entry_volume, actual_balance) if actual_balance > 0 else entry_volume
            
            if sell_volume <= 0:
                result["error"] = f"No balance available for {market} (entry_volume: {entry_volume}, actual_balance: {actual_balance})"
                LOGGER.error(result["error"])
                return result
            
            sell_amount = sell_volume * current_price
            
            # 최소 금액 체크
            if sell_amount < 5000:
                result["error"] = f"Sell amount {sell_amount:.0f}원 < 5,000원 (volume: {sell_volume}, price: {current_price})"
                LOGGER.warning(result["error"])
                return result
            
            LOGGER.info(
                f"Liquidating worst position: {market} "
                f"(PnL: {pnl_pct:.2f}%, Volume: {sell_volume:.6f}, Amount: {sell_amount:.0f}원, "
                f"entry_volume: {entry_volume:.6f}, actual_balance: {actual_balance:.6f})"
            )
            
            # 시장가 매도 (실제 잔액 기준)
            order = self.client.place_order(
                market=market,
                side="ask",
                volume=str(sell_volume),
                ord_type="market",
            )
            
            # 청산 후 포지션 업데이트
            try:
                open_positions = self.trade_history_store.get_open_positions(market=market)
                if open_positions:
                    position_id = open_positions[0]["id"]
                    
                    # 실제 주문 응답에서 체결 금액 확인
                    actual_exit_amount = sell_amount
                    actual_exit_price = current_price
                    actual_exit_volume = sell_volume
                    
                    # 업비트 주문 응답에서 실제 체결 정보 추출
                    if isinstance(order, dict):
                        order_uuid = order.get("uuid")
                        # 업비트 API 응답 구조: avg_price * executed_volume = 실제 체결 금액
                        executed_volume = order.get("executed_volume")
                        avg_price = order.get("avg_price")  # 평균 체결가
                        
                        # 주문 조회 API로 실제 체결 정보 확인 (응답에 체결 정보가 없을 때)
                        if (not executed_volume or not avg_price or float(executed_volume) == 0) and order_uuid:
                            try:
                                import time
                                # 주문 체결 대기 (최대 2초)
                                for _ in range(4):
                                    time.sleep(0.5)
                                    order_info = self.client.get_order(uuid=order_uuid)
                                    if isinstance(order_info, dict):
                                        executed_volume = order_info.get("executed_volume")
                                        avg_price = order_info.get("avg_price")
                                        state = order_info.get("state")
                                        
                                        if state == "done" and executed_volume and avg_price and float(executed_volume) > 0:
                                            LOGGER.info(f"✅ LIQUIDATION: Got executed info from order query")
                                            break
                            except Exception as e:
                                LOGGER.warning(f"Failed to query liquidation order status: {e}")
                        
                        if executed_volume and avg_price and float(executed_volume) > 0:
                            # 실제 체결 정보 사용
                            actual_exit_volume = float(executed_volume)
                            actual_exit_price = float(avg_price)
                            actual_exit_amount = actual_exit_price * actual_exit_volume
                            LOGGER.info(f"✅ LIQUIDATION: Using actual executed data - price: {actual_exit_price:.0f}, volume: {actual_exit_volume:.6f}, amount: {actual_exit_amount:.0f}원 (expected: {sell_amount:.0f}원)")
                        else:
                            LOGGER.warning(f"⚠️ LIQUIDATION: No executed info available, using estimated amount: {sell_amount:.0f}원")
                    
                    self.trade_history_store.close_position(
                        position_id=position_id,
                        exit_price=actual_exit_price,
                        exit_volume=actual_exit_volume,
                        exit_amount=actual_exit_amount,
                    )
                    # 매도 거래 기록
                    balance_before = self.position_sizer.balance_fetcher() if self.position_sizer else None
                    order_id = order.get("uuid") if isinstance(order, dict) else None
                    self.trade_history_store.save_trade(
                        market=market,
                        strategy=self.strategy.name,
                        signal="LIQUIDATE",
                        side="sell",
                        price=actual_exit_price,
                        volume=actual_exit_volume,
                        amount=actual_exit_amount,
                        order_id=order_id,
                        order_response=order,
                        dry_run=self.dry_run,
                        balance_before=balance_before,
                        balance_after=balance_before + actual_exit_amount if balance_before else None,
                    )
                    LOGGER.info(f"Liquidation trade saved: position_id={position_id}")
            except Exception as exc:
                LOGGER.warning(f"Failed to save liquidation trade history: {exc}")
            
            result.update({
                "success": True,
                "market": market,
                "volume": sell_volume,
                "price": current_price,
                "amount": sell_amount,
                "pnl_pct": pnl_pct,
                "order_id": order.get("uuid") if isinstance(order, dict) else None,
                "message": f"{market} 청산 완료 (수익률: {pnl_pct:.2f}%)",
            })
            
            LOGGER.info(
                f"✅ Worst position liquidated: {market} @ {current_price} "
                f"= {sell_amount:.0f}원 (PnL: {pnl_pct:.2f}%)"
            )
            
        except Exception as e:
            result["error"] = str(e)
            LOGGER.error(f"❌ Failed to liquidate worst position: {e}", exc_info=True)
        
        return result
