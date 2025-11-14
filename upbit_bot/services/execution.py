"""Execution loop for polling-based trading."""

from __future__ import annotations

import logging
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
            self.position_price = last_candle.close
            self.position_volume = est_volume
            if self.risk_manager:
                balance = self.position_sizer.balance_fetcher() if self.position_sizer else stake
                stake_pct = (stake / balance * 100) if balance else 0.0
                self.risk_manager.register_entry(self.market, stake_pct=stake_pct)
            
            # Save trade history
            try:
                balance_before = self.position_sizer.balance_fetcher() if self.position_sizer else None
                order_id = response.get("uuid") if isinstance(response, dict) else None
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
                    order_id=order_id,
                    order_response=response,
                    dry_run=self.dry_run,
                    balance_before=balance_before,
                    balance_after=balance_before - stake if balance_before else None,
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
                sell_amount = (self.position_volume or 0.0) * last_candle.close
                self.trade_history_store.close_position(
                    position_id=position_id,
                    exit_price=last_candle.close,
                    exit_volume=self.position_volume or 0.0,
                    exit_amount=sell_amount,
                )
                balance_before = self.position_sizer.balance_fetcher() if self.position_sizer else None
                order_id = response.get("uuid") if isinstance(response, dict) else None
                self.trade_history_store.save_trade(
                    market=self.market,
                    strategy=self.strategy.name,
                    signal=signal.value,
                    side="sell",
                    price=last_candle.close,
                    volume=self.position_volume or 0.0,
                    amount=sell_amount,
                    order_id=order_id,
                    order_response=response,
                    dry_run=self.dry_run,
                    balance_before=balance_before,
                    balance_after=balance_before + sell_amount if balance_before else None,
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
        AI 전략일 때 여러 코인을 분석하여 최적의 코인 선택.
        
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
        
        # AI 전략: 여러 코인 분석
        try:
            # 거래량 상위 10개 코인 가져오기
            markets = self.client.get_top_volume_markets(limit=10)
            LOGGER.info(f"Selected top 10 markets by volume for AI strategy analysis: {markets}")
            
            best_market: str | None = None
            best_signal: StrategySignal = StrategySignal.HOLD
            best_candles: list[Candle] = []
            best_confidence = 0.0
            best_analysis: dict | None = None  # 최고 신뢰도 분석 결과 저장
            
            # 이미 포지션이 있는 코인은 제외
            portfolio = self.get_portfolio_status()
            open_markets = {pos.get("market") for pos in portfolio.get("open_positions", [])}
            
            # 거래량 상위 10개 중에서 포지션이 없는 코인만 분석
            markets_to_analyze = [m for m in markets if m not in open_markets]
            
            LOGGER.info(f"Starting AI analysis for {len(markets_to_analyze)} markets...")
            
            for idx, market in enumerate(markets_to_analyze, 1):
                LOGGER.info(f"[{idx}/{len(markets_to_analyze)}] Analyzing {market}...")
                try:
                    # 캔들 데이터 가져오기 (SSE 스트림용으로는 적은 개수 사용)
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
                    
                    if len(candles_list) < 5:
                        continue
                    
                    # AI 분석 실행
                    signal = self.strategy.on_candles(candles_list)
                    
                    # 분석 결과 가져오기
                    if hasattr(self.strategy, 'last_analysis') and self.strategy.last_analysis:
                        confidence = self.strategy.last_analysis.get('confidence', 0.0)
                        
                        LOGGER.info(f"  {market}: {signal.value} (confidence: {confidence:.2%})")
                        
                        # BUY 신호이고 신뢰도가 더 높으면 업데이트
                        if signal == StrategySignal.BUY and confidence > best_confidence:
                            best_market = market
                            best_signal = signal
                            best_candles = candles_list
                            best_confidence = confidence
                            # 최고 신뢰도 분석 결과 저장
                            best_analysis = self.strategy.last_analysis.copy()
                            
                            LOGGER.info(
                                f"  ✅ Better signal found: {market} {signal.value} "
                                f"(confidence: {confidence:.2%})"
                            )
                    else:
                        LOGGER.warning(f"  {market}: No analysis result available")
                
                except Exception as e:
                    LOGGER.warning(f"Failed to analyze {market}: {e}")
                    continue
            
            if best_market:
                LOGGER.info(
                    f"✅ Selected market: {best_market} with {best_signal.value} "
                    f"(confidence: {best_confidence:.2%})"
                )
                # 선택된 코인으로 market 업데이트
                self.market = best_market
                # 최종 분석 결과를 best_market의 분석 결과로 설정
                if best_analysis:
                    self.strategy.last_analysis = best_analysis
                return best_market, best_signal, best_candles
            else:
                # 신호가 없으면 기존 market 유지
                LOGGER.info("No BUY signal found, using default market")
                candles = self._fetch_candles()
                signal = self.strategy.on_candles(candles)
                return self.market, signal, candles
                
        except Exception as e:
            LOGGER.error(f"Multi-market analysis failed: {e}, using default market")
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
        
        반환:
        - total_positions: 열린 포지션 개수
        - open_positions: 열린 포지션 목록 (수익률 순 정렬)
        - worst_position: 가장 낮은 수익률 포지션
        """
        try:
            positions = self.trade_history_store.get_open_positions()
            
            if not positions:
                return {
                    "total_positions": 0,
                    "open_positions": [],
                    "worst_position": None,
                }
            
            # 각 포지션의 현재가 및 수익률 계산
            positions_with_pnl = []
            
            for pos in positions:
                market = pos.get("market")
                entry_price = float(pos.get("entry_price", 0))
                entry_volume = float(pos.get("entry_volume", 0))
                
                try:
                    # 현재가 조회
                    ticker = self.client.get_ticker(market)
                    if not ticker:
                        continue
                    
                    current_price = float(ticker.get("trade_price", 0))
                    pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                    current_value = entry_volume * current_price
                    
                    positions_with_pnl.append({
                        **pos,
                        "current_price": current_price,
                        "pnl_pct": pnl_pct,
                        "current_value": current_value,
                    })
                    
                except Exception as e:
                    LOGGER.warning(f"Failed to get price for {market}: {e}")
                    continue
            
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
        3. 해당 포지션 시장가 매도
        4. 결과 반환
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
            
            sell_amount = entry_volume * current_price
            
            # 최소 금액 체크
            if sell_amount < 5000:
                result["error"] = f"Sell amount {sell_amount:.0f}원 < 5,000원"
                return result
            
            LOGGER.info(
                f"Liquidating worst position: {market} "
                f"(PnL: {pnl_pct:.2f}%, Amount: {sell_amount:.0f}원)"
            )
            
            # 시장가 매도
            order = self.client.place_order(
                market=market,
                side="ask",
                volume=str(entry_volume),
                ord_type="market",
            )
            
            result.update({
                "success": True,
                "market": market,
                "volume": entry_volume,
                "price": current_price,
                "amount": sell_amount,
                "pnl_pct": pnl_pct,
                "order_id": order.get("uuid"),
                "message": f"{market} 청산 완료 (수익률: {pnl_pct:.2f}%)",
            })
            
            LOGGER.info(
                f"Worst position liquidated: {market} @ {current_price} "
                f"= {sell_amount:.0f}원 (PnL: {pnl_pct:.2f}%)"
            )
            
        except Exception as e:
            result["error"] = str(e)
            LOGGER.error(f"Failed to liquidate worst position: {e}")
        
        return result
