"""동적 모니터링 시스템: 최종 선정 5개 코인에 대한 지속적 관찰 및 AI 타이밍 판단."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from datetime import datetime, UTC
from typing import Any, Callable

from upbit_bot.strategies import Candle

LOGGER = logging.getLogger(__name__)


class DynamicTradingMonitor:
    """최종 선정 5개 코인에 대한 동적 모니터링."""

    def __init__(self, check_interval: int = 60, high_risk: bool = False):
        """
        Args:
            check_interval: 모니터링 주기 (초, 기본값: 60초)
            high_risk: 고위험 전략 여부 (False면 저위험)
        """
        self.check_interval = check_interval
        self.high_risk = high_risk
        self.monitored_coins: dict[str, dict[str, Any]] = {}  # {market: {data}}
        self.price_history: dict[str, deque] = {}  # 가격 변화 추적
        self.candle_history: dict[str, deque] = {}  # 캔들 데이터 추적
        self.lock = threading.Lock()
        self._monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._price_callback: Callable[[str], float | None] | None = None
        self._candle_callback: Callable[[str], list[Candle] | None] | None = None

    def update_final_candidates(self, final_candidates: list[dict[str, Any]]) -> None:
        """
        최종 선정 5개 업데이트 (동적으로 변경 가능).
        
        Args:
            final_candidates: 최종 선정된 5개 코인 리스트 (AI 타이밍 정보 포함)
        """
        with self.lock:
            # 기존 모니터링 중인 코인과 비교
            new_markets = {c.get("market") for c in final_candidates if c.get("market")}
            old_markets = set(self.monitored_coins.keys())
            
            # 새로 추가된 코인
            added = new_markets - old_markets
            # 제거된 코인
            removed = old_markets - new_markets
            
            if added:
                LOGGER.info(f"모니터링 추가: {added}")
            if removed:
                LOGGER.info(f"모니터링 제거: {removed}")
            
            # 모니터링 목록 업데이트
            self.monitored_coins = {}
            for c in final_candidates:
                market = c.get("market")
                if not market:
                    continue
                
                self.monitored_coins[market] = {
                    "score": c.get("score_eff", c.get("score", 0.0)),
                    "buy_signal": c.get("buy_signal", "none"),
                    "buy_timing": c.get("buy_timing", "wait"),  # AI 타이밍 판단
                    "timing_reason": c.get("timing_reason", ""),
                    "last_update": datetime.now(UTC),
                    "entry_signal": None,  # 매수 시그널 대기
                    "candidate_data": c,  # 전체 후보 데이터 저장
                }
                
                # 가격 히스토리 초기화 (새 코인만)
                if market not in self.price_history:
                    self.price_history[market] = deque(maxlen=20)  # 최근 20개 가격
                    self.candle_history[market] = deque(maxlen=10)  # 최근 10개 캔들

    def set_callbacks(
        self,
        price_callback: Callable[[str], float | None],
        candle_callback: Callable[[str], list[Candle] | None] | None = None,
    ) -> None:
        """
        가격 조회 및 캔들 데이터 조회 콜백 설정.
        
        Args:
            price_callback: 현재 가격을 반환하는 함수
            candle_callback: 캔들 데이터를 반환하는 함수 (선택사항)
        """
        self._price_callback = price_callback
        self._candle_callback = candle_callback

    def start_monitoring(self) -> None:
        """모니터링 시작."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        
        if not self._price_callback:
            LOGGER.warning("가격 콜백이 설정되지 않아 모니터링을 시작할 수 없습니다")
            return

        def monitor_loop() -> None:
            LOGGER.info(f"동적 모니터링 시작 (주기: {self.check_interval}초)")
            
            while not self._stop_event.is_set():
                try:
                    with self.lock:
                        markets = list(self.monitored_coins.keys())
                    
                    if not markets:
                        # 모니터링할 코인이 없으면 대기
                        self._stop_event.wait(self.check_interval)
                        continue
                    
                    # 각 코인의 현재 가격 및 캔들 확인
                    for market in markets:
                        try:
                            # 현재 가격 조회 (빠른 API 호출)
                            current_price = self._price_callback(market)
                            if current_price is None:
                                continue
                            
                            # 가격 히스토리 업데이트
                            if market not in self.price_history:
                                self.price_history[market] = deque(maxlen=20)
                            
                            self.price_history[market].append({
                                "price": current_price,
                                "timestamp": datetime.now(UTC),
                            })
                            
                            # 캔들 데이터 조회 (있는 경우)
                            candles = None
                            if self._candle_callback:
                                candles = self._candle_callback(market)
                                if candles and market not in self.candle_history:
                                    self.candle_history[market] = deque(maxlen=10)
                                if candles:
                                    self.candle_history[market].append({
                                        "candles": candles,
                                        "timestamp": datetime.now(UTC),
                                    })
                            
                            # AI 타이밍 판단 및 매수 타이밍 체크
                            entry_signal = self._check_buy_timing(market, current_price, candles)
                            
                            if entry_signal:
                                with self.lock:
                                    if market in self.monitored_coins:
                                        self.monitored_coins[market]["entry_signal"] = entry_signal
                                
                                LOGGER.info(
                                    f"매수 타이밍 감지: {market} - "
                                    f"타이밍: {entry_signal.get('timing')}, "
                                    f"신호: {entry_signal.get('signal')}, "
                                    f"이유: {entry_signal.get('reason')}"
                                )
                                
                        except Exception as e:
                            LOGGER.warning(f"모니터링 오류 ({market}): {e}")
                    
                    # 주기 대기
                    self._stop_event.wait(self.check_interval)
                    
                except Exception as e:
                    LOGGER.error(f"모니터링 루프 오류: {e}", exc_info=True)
                    self._stop_event.wait(self.check_interval)
            
            LOGGER.info("동적 모니터링 종료")
        
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self._monitor_thread.start()
        LOGGER.info("동적 모니터링 시작됨")

    def stop_monitoring(self) -> None:
        """모니터링 중지."""
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._stop_event.set()
            self._monitor_thread.join(timeout=5)
            LOGGER.info("동적 모니터링 중지됨")

    def _check_buy_timing(
        self,
        market: str,
        current_price: float,
        candles: list[Candle] | None = None,
    ) -> dict[str, Any] | None:
        """
        AI 타이밍 판단에 따른 매수 타이밍 체크.
        
        Args:
            market: 코인 마켓 코드
            current_price: 현재 가격
            candles: 캔들 데이터 (선택사항)
        
        Returns:
            매수 시그널 또는 None
        """
        with self.lock:
            if market not in self.monitored_coins:
                return None
            
            coin_data = self.monitored_coins[market]
            buy_timing = coin_data.get("buy_timing", "wait")  # AI 타이밍 판단
            buy_signal = coin_data.get("buy_signal", "none")
        
        # AI 타이밍 판단 우선 적용
        if buy_timing == "now":
            # 즉시 매수 타이밍
            return {
                "type": "ai_timing",
                "timing": "now",
                "signal": buy_signal,
                "reason": coin_data.get("timing_reason", "AI 즉시 매수 타이밍 판단"),
            }
        
        elif buy_timing == "watch":
            # 관찰 중: 가격 변동 및 조건 체크 (전략별 차별화)
            # 가격 히스토리 요구사항 완화: 3개 → 1개 (즉시 체크 가능)
            if market not in self.price_history or len(self.price_history[market]) < 1:
                # 히스토리가 없으면 현재 가격만으로도 체크 (최소 1개)
                if market not in self.price_history:
                    return None
                # 히스토리가 1개만 있어도 체크 가능하도록 수정
                if len(self.price_history[market]) < 1:
                    return None
            
            history = list(self.price_history[market])
            recent_prices = [h["price"] for h in history[-5:]]
            
            # 가격 변화 분석 (최소 2개 이상 필요)
            if len(recent_prices) < 2:
                # 가격이 1개만 있으면 시간 기반 자동 매수 고려
                with self.lock:
                    coin_data = self.monitored_coins.get(market, {})
                monitored_since = coin_data.get("last_update")
                if monitored_since:
                    elapsed_minutes = (datetime.now(UTC) - monitored_since).total_seconds() / 60
                    # 시간 기반 자동 매수 (전략별 차별화)
                    if self.high_risk:
                        # 고위험: 2분 이상 관찰 후 strong/medium 신호면 매수
                        if elapsed_minutes >= 2 and buy_signal in ("strong", "medium"):
                            return {
                                "type": "watch_timeout",
                                "timing": "watch",
                                "signal": buy_signal,
                                "reason": f"[고위험] 관찰 중 {elapsed_minutes:.1f}분 경과, {buy_signal} 신호로 매수",
                            }
                    else:
                        # 저위험: 5분 이상 관찰 후 strong 신호만 매수
                        if elapsed_minutes >= 5 and buy_signal == "strong":
                            return {
                                "type": "watch_timeout",
                                "timing": "watch",
                                "signal": buy_signal,
                                "reason": f"[저위험] 관찰 중 {elapsed_minutes:.1f}분 경과, strong 신호로 매수",
                            }
                return None
            
            price_change = (recent_prices[-1] - recent_prices[0]) / recent_prices[0] * 100 if recent_prices[0] > 0 else 0
            
            if self.high_risk:
                # 고위험 전략: 공격적 진입 (조건 완화)
                # 0.5% 이상 변동이면 진입 고려
                if price_change < -1.0 and len(recent_prices) >= 2:
                    # 급락 후 반등 체크 (완화된 조건)
                    rebound = (recent_prices[-1] - min(recent_prices[-3:])) / min(recent_prices[-3:]) * 100
                    if rebound > 0.5:  # 1.0% → 0.5%로 완화
                        return {
                            "type": "watch_rebound",
                            "timing": "watch",
                            "signal": "medium",
                            "reason": f"[고위험] 관찰 중 급락 후 반등 감지 ({price_change:.2f}% → {rebound:.2f}% 반등)",
                        }
                elif price_change > 0.5:  # 1.5% → 0.5%로 완화
                    # 지속 상승 (완화된 조건)
                    return {
                        "type": "watch_momentum",
                        "timing": "watch",
                        "signal": "strong",
                        "reason": f"[고위험] 관찰 중 지속 상승 감지 ({price_change:.2f}%)",
                    }
            else:
                # 저위험 전략: 보수적 진입 (엄격한 조건)
                # 2% 이상 급락 후 1% 이상 반등 또는 1.5% 이상 상승
                if price_change < -2.0 and len(recent_prices) >= 2:
                    # 급락 후 반등 체크
                    rebound = (recent_prices[-1] - min(recent_prices[-3:])) / min(recent_prices[-3:]) * 100
                    if rebound > 1.0:
                        return {
                            "type": "watch_rebound",
                            "timing": "watch",
                            "signal": "medium",
                            "reason": f"[저위험] 관찰 중 급락 후 반등 감지 ({price_change:.2f}% → {rebound:.2f}% 반등)",
                        }
                elif price_change > 1.5:
                    # 지속 상승
                    return {
                        "type": "watch_momentum",
                        "timing": "watch",
                        "signal": "strong",
                        "reason": f"[저위험] 관찰 중 지속 상승 감지 ({price_change:.2f}%)",
                    }
        
        elif buy_timing == "wait":
            # 대기 중: 더 나은 진입점 대기 (전략별 차별화)
            # 가격 히스토리 요구사항 완화: 5개 → 2개
            if market not in self.price_history or len(self.price_history[market]) < 2:
                return None
            
            history = list(self.price_history[market])
            recent_prices = [h["price"] for h in history[-5:]]
            price_change = (recent_prices[-1] - recent_prices[0]) / recent_prices[0] * 100 if recent_prices[0] > 0 else 0
            
            if self.high_risk:
                # 고위험 전략: 2% 이상 급락 시 매수 기회로 판단 (완화된 조건)
                if price_change < -2.0:  # -5.0% → -2.0%로 완화
                    return {
                        "type": "wait_opportunity",
                        "timing": "wait",
                        "signal": "medium",
                        "reason": f"[고위험] 대기 중 급락 감지, 매수 기회 ({price_change:.2f}%)",
                    }
            else:
                # 저위험 전략: wait 상태에서는 진입 금지 (더 나은 기회 대기)
                # 큰 급락(-5% 이상)이어도 진입하지 않음
                return None
        
        return None

    def get_monitoring_status(self) -> dict[str, Any]:
        """모니터링 상태 반환."""
        with self.lock:
            return {
                "monitored_count": len(self.monitored_coins),
                "markets": list(self.monitored_coins.keys()),
                "signals": {
                    market: data.get("entry_signal")
                    for market, data in self.monitored_coins.items()
                    if data.get("entry_signal")
                },
                "timings": {
                    market: {
                        "buy_timing": data.get("buy_timing"),
                        "buy_signal": data.get("buy_signal"),
                        "timing_reason": data.get("timing_reason"),
                    }
                    for market, data in self.monitored_coins.items()
                },
            }

    def get_entry_signal(self, market: str) -> dict[str, Any] | None:
        """
        특정 코인의 매수 시그널 가져오기 (시그널 후 초기화).
        
        Args:
            market: 코인 마켓 코드
        
        Returns:
            매수 시그널 또는 None
        """
        with self.lock:
            if market not in self.monitored_coins:
                return None
            
            signal = self.monitored_coins[market].get("entry_signal")
            if signal:
                # 시그널 반환 후 초기화 (중복 실행 방지)
                self.monitored_coins[market]["entry_signal"] = None
                return signal
        
        return None
    
    def clear_entry_signal(self, market: str) -> None:
        """특정 마켓의 매수 시그널 초기화 (매매 완료 후 호출)."""
        with self.lock:
            if market in self.monitored_coins:
                self.monitored_coins[market]["entry_signal"] = None
                LOGGER.debug(f"매수 시그널 초기화: {market}")

