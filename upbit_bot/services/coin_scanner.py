"""Ollama 1: 코인 스캐너 (정보 수집용 - 1.5b 모델)."""

from __future__ import annotations

import logging
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, UTC
from typing import Any

import requests

from upbit_bot.strategies import Candle

from .ollama_client import OllamaClient, OllamaError, OLLAMA_BASE_URL, OLLAMA_SCANNER_MODEL
from .sentiment_crawler import SentimentCrawler

LOGGER = logging.getLogger(__name__)


class CoinScanner:
    """여러 코인을 스캔하고 정보를 수집하는 Ollama 인스턴스."""

    def __init__(
        self,
        ollama_url: str | None = None,
        model: str | None = None,
        timeout: int = 120,  # 타임아웃 120초 (Ollama 서버 응답 지연 대응)
        fallback_url: str | None = None,  # Fallback Ollama URL (노트북 실패 시 서버로 전환)
    ) -> None:
        # ollama_url과 model이 None이면 기본값 사용
        url = ollama_url or OLLAMA_BASE_URL
        model_name = model or OLLAMA_SCANNER_MODEL
        
        # 연결 상태 사전 확인 후 사용 가능한 URL 선택
        primary_url = url
        self.fallback_url = fallback_url
        self.primary_url = url
        
        if fallback_url:
            # Primary URL 연결 확인 (빠른 타임아웃: 3초)
            temp_client = OllamaClient(base_url=primary_url, model=model_name, timeout=3)
            if not temp_client.verify_connection(quick_check=True):
                LOGGER.warning(
                    f"Primary Ollama 연결 실패 ({primary_url}), Fallback 사용 ({fallback_url})"
                )
                primary_url = fallback_url
            else:
                LOGGER.debug(f"Primary Ollama 연결 확인됨 ({primary_url})")
        
        self.client = OllamaClient(base_url=primary_url, model=model_name, timeout=timeout)
        self.last_scan_result: dict[str, dict[str, Any]] | None = None
        self.last_scan_time: datetime | None = None
        self._stop_event = threading.Event()
        self._scan_thread: threading.Thread | None = None
        self._scan_lock = threading.Lock()
        self._markets_data_callback: Any = None  # markets_data를 가져오는 콜백 함수
        
        # 감정 지표 크롤러 초기화 (선택적)
        self.sentiment_crawler: SentimentCrawler | None = None
        try:
            self.sentiment_crawler = SentimentCrawler(timeout=5, cache_ttl=1800)  # 5초 타임아웃, 30분 캐시
        except Exception as e:
            LOGGER.warning(f"감정 지표 크롤러 초기화 실패: {e}, 감정 지표 없이 진행")
            self.sentiment_crawler = None

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

    def fast_filter_by_indicators(
        self, 
        markets_data: dict[str, list[Candle]],
        top_n: int = 30
    ) -> dict[str, dict[str, Any]]:
        """
        기술적 지표만으로 빠르게 필터링 (Ollama 호출 없음).
        
        필터링 기준:
        1. 기술적 지표 점수 계산 (가중치 기반)
        2. 상위 N개 선정
        
        Args:
            markets_data: {market: [candles]} 딕셔너리
            top_n: 선정할 코인 개수 (기본값: 30)
        
        Returns:
            {market: {score, reason, trend, risk, indicators}} 딕셔너리
        """
        results: dict[str, dict[str, Any]] = {}
        
        LOGGER.info(f"1차 빠른 필터링 시작: {len(markets_data)}개 코인 (기술적 지표만, Ollama 없음)")
        
        for market, candles in markets_data.items():
            if len(candles) < 5:
                continue
            
            indicators = self.calculate_indicators(candles)
            if not indicators:
                continue
            
            # 기술적 지표 기반 점수 계산 (감정 지표는 나중에 추가)
            score = self._calculate_technical_score(indicators, sentiment=0.5)  # 기본값 0.5
            risk = self._estimate_risk(indicators)
            reason = self._generate_technical_reason(indicators)
            
            results[market] = {
                "score": score,
                "indicators": indicators,
                "trend": indicators.get("trend", "unknown"),
                "risk": risk,
                "reason": reason,
                "sentiment": 0.5,  # 기본값 (나중에 업데이트)
            }
        
        # 기술적 지표 점수 기준 정렬 후 상위 N개 선정
        sorted_results = dict(
            sorted(results.items(), key=lambda x: x[1]["score"], reverse=True)[:top_n]
        )
        
        # Reddit 감정 지표 크롤링 (보조 지표로 반영)
        if self.sentiment_crawler and sorted_results:
            try:
                # 코인 심볼 추출 (KRW-BTC -> BTC)
                coin_symbols = [
                    market.replace("KRW-", "") for market in sorted_results.keys()
                ]
                
                # Reddit 크롤링 (상위 10개만 크롤링하여 검토 시간 단축)
                # 검토 시간 최소화: 상위 10개만, 빠른 타임아웃, 캐시 활용
                LOGGER.info("Reddit 감정 지표 크롤링 시작 (보조 지표, 상위 10개만)")
                sentiment_results = self.sentiment_crawler.crawl_multiple_coins(
                    coin_symbols=coin_symbols,
                    max_workers=3,  # Reddit rate limit 고려 (3개 제한)
                    limit_per_coin=15,  # 게시물 수 최소화 (15개로 감소)
                    top_n_only=10,  # 상위 10개만 크롤링 (검토 시간 단축: 30초 이내)
                )
                
                # 감정 지표를 결과에 반영 및 점수 재계산
                for market in sorted_results.keys():
                    coin_symbol = market.replace("KRW-", "")
                    sentiment_data = sentiment_results.get(coin_symbol, {})
                    sentiment_score = sentiment_data.get("sentiment", 0.5)  # 기본값 0.5 (중립)
                    
                    # 감정 지표를 보조 지표로 추가
                    sorted_results[market]["sentiment"] = sentiment_score
                    sorted_results[market]["sentiment_source"] = sentiment_data.get("source", "none")
                    sorted_results[market]["sentiment_post_count"] = sentiment_data.get("post_count", 0)
                    
                    # 감정 지표 반영하여 점수 재계산 (보조 지표 10% 가중치)
                    indicators = sorted_results[market].get("indicators", {})
                    updated_score = self._calculate_technical_score(indicators, sentiment=sentiment_score)
                    sorted_results[market]["score"] = updated_score
                    sorted_results[market]["score_technical"] = self._calculate_technical_score(indicators, sentiment=0.5)  # 원래 점수 저장
                
                # 감정 지표 반영 후 점수 기준 재정렬
                sorted_results = dict(
                    sorted(sorted_results.items(), key=lambda x: x[1]["score"], reverse=True)
                )
                
                LOGGER.info(
                    f"Reddit 감정 지표 크롤링 완료: "
                    f"{sum(1 for r in sorted_results.values() if 'sentiment' in r)}개 코인 분석됨"
                )
                
            except Exception as e:
                LOGGER.warning(f"Reddit 감정 지표 크롤링 실패: {e}, 감정 지표 없이 진행")
                # 실패 시 기본값 설정
                for market in sorted_results.keys():
                    sorted_results[market]["sentiment"] = 0.5
                    sorted_results[market]["sentiment_source"] = "none"
                    sorted_results[market]["sentiment_post_count"] = 0
        else:
            # 감정 지표 크롤러가 없으면 기본값 설정
            for market in sorted_results.keys():
                sorted_results[market]["sentiment"] = 0.5
                sorted_results[market]["sentiment_source"] = "none"
                sorted_results[market]["sentiment_post_count"] = 0
        
        LOGGER.info(
            f"1차 빠른 필터링 완료: {len(sorted_results)}개 코인 선정 "
            f"(최고 점수: {max(r['score'] for r in sorted_results.values()):.2f})"
        )
        
        return sorted_results

    def _calculate_technical_score(
        self, 
        indicators: dict[str, Any],
        sentiment: float = 0.5  # 감정 지표 (기본값 0.5: 중립)
    ) -> float:
        """
        기술적 지표 + 감정 지표로 점수 계산 (가중치 기반).
        
        Args:
            indicators: 기술적 지표 딕셔너리
            sentiment: 감정 지표 (0.0: 부정, 1.0: 긍정, 기본값: 0.5)
        
        Returns:
            종합 점수 (0.0 ~ 1.0)
        """
        technical_score = 0.0
        
        # 트렌드 점수 (35%, 감정 지표 반영으로 약간 감소)
        if indicators.get("trend") == "uptrend":
            technical_score += 0.35
        elif indicators.get("trend") == "downtrend":
            technical_score += 0.1
        
        # 가격 변화 점수 (25%, 감정 지표 반영으로 약간 감소)
        recent_change = indicators.get("recent_change", 0.0)
        if recent_change > 5:
            technical_score += 0.25
        elif recent_change > 0:
            technical_score += 0.15
        elif recent_change > -5:
            technical_score += 0.05
        
        # 거래량 점수 (18%, 감정 지표 반영으로 약간 감소)
        volume_ratio = indicators.get("volume_ratio", 1.0)
        if volume_ratio > 2.0:
            technical_score += 0.18
        elif volume_ratio > 1.5:
            technical_score += 0.15
        elif volume_ratio > 1.0:
            technical_score += 0.1
        
        # 변동성 점수 (12%, 감정 지표 반영으로 약간 증가)
        volatility = indicators.get("volatility", 0.0)
        if 2.0 <= volatility <= 5.0:
            technical_score += 0.12
        elif 1.0 <= volatility < 2.0 or 5.0 < volatility <= 8.0:
            technical_score += 0.06
        
        # 감정 지표 반영 (10%, 보조 지표)
        # sentiment가 0.5보다 크면 긍정, 작으면 부정
        sentiment_score = (sentiment - 0.5) * 0.2  # -0.1 ~ +0.1 범위로 변환
        technical_score += sentiment_score
        
        return min(max(technical_score, 0.0), 1.0)  # 0.0 ~ 1.0 범위로 제한

    def _estimate_risk(self, indicators: dict[str, Any]) -> str:
        """변동성 기반 리스크 추정"""
        volatility = indicators.get("volatility", 0.0)
        if volatility > 8.0:
            return "high"
        elif volatility > 5.0:
            return "medium"
        else:
            return "low"

    def _generate_technical_reason(self, indicators: dict[str, Any]) -> str:
        """기술적 지표 기반 이유 생성"""
        reasons = []
        
        if indicators.get("trend") == "uptrend":
            reasons.append("상승 추세")
        
        if indicators.get("recent_change", 0) > 5:
            reasons.append("급등 중")
        elif indicators.get("recent_change", 0) < -5:
            reasons.append("급락 중")
        
        if indicators.get("volume_ratio", 1.0) > 2.0:
            reasons.append("거래량 급증")
        
        return ", ".join(reasons) if reasons else "보통"

    def scan_single_market(self, market: str, candles: list[Candle]) -> dict[str, Any] | None:
        """
        단일 코인 스캔 (재시도 + Fallback 포함).
        
        Args:
            market: 코인 마켓 코드
            candles: 캔들 데이터
        
        Returns:
            스캔 결과 또는 None (실패 시)
        """
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

        max_retries = 2
        for attempt in range(max_retries):
            try:
                response_text = self.client.generate(prompt, temperature=0.3, max_retries=1)
                data = self.client.parse_json_response(response_text)

                return {
                    "score": float(data.get("score", 0.0)),
                    "reason": data.get("reason", ""),
                    "trend": data.get("trend", indicators["trend"]),
                    "risk": data.get("risk", "medium"),
                    "indicators": indicators,
                }

            except (OllamaError, requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                if attempt < max_retries - 1:
                    # Fallback URL로 전환 시도
                    if self.fallback_url and self.client.base_url != self.fallback_url:
                        LOGGER.warning(
                            f"스캔 실패 (시도 {attempt + 1}/{max_retries}), Fallback으로 전환: {e}"
                        )
                        from .ollama_client import OLLAMA_SCANNER_MODEL
                        self.client = OllamaClient(
                            base_url=self.fallback_url,
                            model=self.client.model or OLLAMA_SCANNER_MODEL,
                            timeout=self.client.timeout
                        )
                        continue
                    else:
                        # 짧은 대기 후 재시도
                        wait_time = 1 * (attempt + 1)
                        LOGGER.warning(f"스캔 실패 (시도 {attempt + 1}/{max_retries}), {wait_time}초 후 재시도: {e}")
                        time.sleep(wait_time)
                        continue
                else:
                    LOGGER.warning(f"코인 {market} 스캔 최종 실패: {e}")
                    return None
            except Exception as e:
                LOGGER.warning(f"코인 {market} 스캔 중 예기치 않은 오류: {e}")
                return None
        
        return None

    def scan_markets(
        self, markets_data: dict[str, list[Candle]], max_workers: int = 2
    ) -> dict[str, dict[str, Any]]:
        """
        여러 코인을 병렬로 스캔하여 정보 수집.

        Args:
            markets_data: {market: [candles]} 딕셔너리
            max_workers: 최대 동시 처리 수 (기본값: 2, Ollama 서버 부하 최소화)

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
            failed = 0
            try:
                for future in as_completed(futures, timeout=300):  # 전체 타임아웃 5분
                    completed += 1
                    try:
                        market, result = future.result(timeout=1)  # 개별 결과 타임아웃 1초
                        if result:
                            with results_lock:
                                results[market] = result
                                LOGGER.debug(
                                    f"[{completed}/{len(markets_data)}] {market}: "
                                    f"score={result['score']:.2f}, "
                                    f"trend={result['trend']}, risk={result['risk']}"
                                )
                        else:
                            failed += 1
                        
                        # 10개마다 진행 상황 로그
                        if completed % 10 == 0 or completed == len(markets_data):
                            LOGGER.info(f"스캔 진행: {completed}/{len(markets_data)} 완료 ({len(results)}개 성공, {failed}개 실패)")
                    except Exception as e:
                        market = futures.get(future, "unknown")
                        failed += 1
                        LOGGER.warning(f"코인 {market} 스캔 처리 중 오류: {e}")
                    except TimeoutError:
                        market = futures.get(future, "unknown")
                        failed += 1
                        LOGGER.warning(f"코인 {market} 스캔 타임아웃")
            except TimeoutError:
                LOGGER.warning(f"전체 스캔 타임아웃 (5분), 완료된 {len(results)}개 결과 반환")

        self.last_scan_result = results
        self.last_scan_time = datetime.now(UTC)
        success_rate = (len(results) / len(markets_data) * 100) if markets_data else 0.0
        LOGGER.info(f"코인 스캔 완료: {len(results)}개 코인 분석됨 (성공률: {success_rate:.1f}%, 실패: {failed}개)")
        
        # 최소 1개 이상 성공했거나, 실패했어도 빈 결과 반환 (UI 업데이트를 위해)
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

