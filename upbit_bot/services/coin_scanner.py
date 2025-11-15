"""Ollama 1: 코인 스캐너 (정보 수집용 - 1.5b 모델)."""

from __future__ import annotations

import logging
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, UTC
from typing import Any

from upbit_bot.strategies import Candle

from .ollama_client import OllamaClient, OllamaError, OLLAMA_BASE_URL, OLLAMA_SCANNER_MODEL

LOGGER = logging.getLogger(__name__)


class CoinScanner:
    """여러 코인을 스캔하고 정보를 수집하는 Ollama 인스턴스."""

    def __init__(
        self,
        ollama_url: str | None = None,
        model: str | None = None,
        timeout: int = 90,  # 타임아웃 90초 (Ollama 서버 응답 대기 - 네트워크 지연 고려)
    ) -> None:
        # ollama_url과 model이 None이면 기본값 사용
        url = ollama_url or OLLAMA_BASE_URL
        model_name = model or OLLAMA_SCANNER_MODEL
        self.client = OllamaClient(base_url=url, model=model_name, timeout=timeout)
        self.last_scan_result: dict[str, dict[str, Any]] | None = None
        self.last_scan_time: datetime | None = None
        self._stop_event = threading.Event()
        self._scan_thread: threading.Thread | None = None
        self._scan_lock = threading.Lock()
        self._markets_data_callback: Any = None  # markets_data를 가져오는 콜백 함수

    def calculate_indicators(self, candles: list[Candle]) -> dict[str, Any]:
        """기술적 지표 계산."""
        if len(candles) < 5:
            return {}

        closes = [c.close for c in candles]
        volumes = [c.volume for c in candles]

        # 이동평균선
        ma_5 = statistics.mean(closes[-5:]) if len(closes) >= 5 else closes[-1]
        ma_10 = statistics.mean(closes[-10:]) if len(closes) >= 10 else closes[-1]
        ma_20 = statistics.mean(closes[-20:]) if len(closes) >= 20 else closes[-1]

        # 변동성 (표준편차)
        volatility = (
            (statistics.stdev(closes[-20:]) / ma_20 * 100) if len(closes) >= 20 else 0.0
        )

        # 최근 가격 변화
        recent_change = ((closes[-1] - closes[-5]) / closes[-5] * 100) if len(closes) >= 5 else 0.0

        # 평균 거래량
        avg_volume = statistics.mean(volumes[-10:]) if len(volumes) >= 10 else volumes[-1]
        current_volume = volumes[-1]
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0

        return {
            "current_price": closes[-1],
            "ma_5": ma_5,
            "ma_10": ma_10,
            "ma_20": ma_20,
            "volatility": volatility,
            "recent_change": recent_change,
            "volume_ratio": volume_ratio,
            "trend": "uptrend" if ma_5 > ma_10 > ma_20 else "downtrend",
        }

    def scan_single_market(self, market: str, candles: list[Candle]) -> dict[str, Any] | None:
        """단일 코인 스캔."""
        if len(candles) < 5:
            return None

        indicators = self.calculate_indicators(candles)
        if not indicators:
            return None

        prompt = f"""당신은 암호화폐 시장 스캐너입니다.
다음 코인의 기술적 지표를 분석하고 점수(0.0~1.0)와 이유를 제시하세요.

코인: {market}
시장 데이터:
- 현재 가격: {indicators['current_price']:,.0f} 원
- 5분 이동평균: {indicators['ma_5']:,.0f}
- 10분 이동평균: {indicators['ma_10']:,.0f}
- 20분 이동평균: {indicators['ma_20']:,.0f}
- 변동성: {indicators['volatility']:.2f}%
- 최근 5분 변화: {indicators['recent_change']:+.2f}%
- 거래량 비율: {indicators['volume_ratio']:.2f}x
- 트렌드: {indicators['trend']}

다음 JSON 형식으로 응답하세요:
{{"score": 0.0~1.0, "reason": "이유", "trend": "uptrend|downtrend", "risk": "low|medium|high"}}"""

        try:
            response_text = self.client.generate(prompt, temperature=0.3)
            data = self.client.parse_json_response(response_text)

            return {
                "score": float(data.get("score", 0.0)),
                "reason": data.get("reason", ""),
                "trend": data.get("trend", indicators["trend"]),
                "risk": data.get("risk", "medium"),
                "indicators": indicators,
            }

        except OllamaError as e:
            LOGGER.warning(f"코인 {market} 스캔 실패: {e}")
            return None

    def scan_markets(
        self, markets_data: dict[str, list[Candle]], max_workers: int = 5
    ) -> dict[str, dict[str, Any]]:
        """
        여러 코인을 병렬로 스캔하여 정보 수집.

        Args:
            markets_data: {market: [candles]} 딕셔너리
            max_workers: 최대 동시 처리 수 (기본값: 5, Ollama 서버 부하 고려)

        Returns:
            {market: {score, reason, trend, risk, indicators}} 딕셔너리
        """
        results: dict[str, dict[str, Any]] = {}
        results_lock = threading.Lock()

        LOGGER.info(f"코인 스캔 시작: {len(markets_data)}개 코인 (병렬 처리: {max_workers}개 동시)")

        def scan_one(market: str, candles: list[Candle]) -> tuple[str, dict[str, Any] | None]:
            """단일 코인 스캔 래퍼 함수 (병렬 처리용)"""
            try:
                result = self.scan_single_market(market, candles)
                return market, result
            except Exception as e:
                LOGGER.warning(f"코인 {market} 스캔 실패: {e}")
                return market, None

        # 병렬 처리 실행
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(scan_one, market, candles): market
                for market, candles in markets_data.items()
            }

            completed = 0
            for future in as_completed(futures):
                completed += 1
                try:
                    market, result = future.result()
                    if result:
                        with results_lock:
                            results[market] = result
                            LOGGER.debug(
                                f"[{completed}/{len(markets_data)}] {market}: "
                                f"score={result['score']:.2f}, "
                                f"trend={result['trend']}, risk={result['risk']}"
                            )
                    
                    # 10개마다 진행 상황 로그
                    if completed % 10 == 0 or completed == len(markets_data):
                        LOGGER.info(f"스캔 진행: {completed}/{len(markets_data)} 완료 ({len(results)}개 성공)")
                except Exception as e:
                    market = futures[future]
                    LOGGER.warning(f"코인 {market} 스캔 처리 중 오류: {e}")

        self.last_scan_result = results
        self.last_scan_time = datetime.now(UTC)
        LOGGER.info(f"코인 스캔 완료: {len(results)}개 코인 분석됨 (성공률: {len(results)/len(markets_data)*100:.1f}%)")
        return results
    
    def start_background_scanning(
        self,
        get_markets_data_callback: Any,
        interval_seconds: int = 300,  # 5분마다 스캔
    ) -> None:
        """
        백그라운드에서 지속적으로 코인 스캔 시작 (서버 시작/종료와 별개).
        
        Args:
            get_markets_data_callback: markets_data를 반환하는 함수
            interval_seconds: 스캔 간격 (초, 기본값: 300초 = 5분)
        """
        if self._scan_thread and self._scan_thread.is_alive():
            LOGGER.info("Ollama 1 백그라운드 스캔이 이미 실행 중입니다.")
            return
        
        self._markets_data_callback = get_markets_data_callback
        self._stop_event.clear()
        
        def background_scan_loop() -> None:
            """백그라운드 스캔 루프"""
            LOGGER.info(f"Ollama 1 백그라운드 스캔 시작 (간격: {interval_seconds}초)")
            
            while not self._stop_event.is_set():
                try:
                    # markets_data 가져오기
                    if self._markets_data_callback:
                        markets_data = self._markets_data_callback()
                        if markets_data:
                            with self._scan_lock:
                                # 스캔 실행
                                self.scan_markets(markets_data)
                                LOGGER.info(
                                    f"Ollama 1 백그라운드 스캔 완료: "
                                    f"{len(self.last_scan_result or {})}개 코인 분석됨 "
                                    f"(다음 스캔: {interval_seconds}초 후)"
                                )
                    else:
                        LOGGER.warning("Ollama 1 백그라운드 스캔: markets_data 콜백이 없습니다.")
                    
                    # interval_seconds 동안 대기 (중간에 stop 이벤트 체크)
                    for _ in range(interval_seconds):
                        if self._stop_event.is_set():
                            break
                        time.sleep(1)
                        
                except Exception as e:
                    LOGGER.error(f"Ollama 1 백그라운드 스캔 오류: {e}", exc_info=True)
                    # 오류 발생 시에도 계속 실행
                    time.sleep(60)
            
            LOGGER.info("Ollama 1 백그라운드 스캔 종료")
        
        self._scan_thread = threading.Thread(target=background_scan_loop, daemon=True)
        self._scan_thread.start()
        LOGGER.info("Ollama 1 백그라운드 스캔 스레드 시작됨")
    
    def stop_background_scanning(self) -> None:
        """백그라운드 스캔 중지."""
        if self._scan_thread and self._scan_thread.is_alive():
            self._stop_event.set()
            self._scan_thread.join(timeout=5)
            LOGGER.info("Ollama 1 백그라운드 스캔 중지됨")
    
    def get_last_scan_result(self) -> dict[str, dict[str, Any]] | None:
        """최신 스캔 결과 반환."""
        with self._scan_lock:
            return self.last_scan_result

