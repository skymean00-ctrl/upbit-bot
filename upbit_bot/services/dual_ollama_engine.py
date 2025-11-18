"""이중 Ollama 엔진: CoinScanner + TradingDecisionMaker 통합."""

from __future__ import annotations

import logging
import os
from typing import Any

from upbit_bot.strategies import Candle, StrategySignal

from .coin_scanner import CoinScanner
from .ollama_client import OLLAMA_BASE_URL
from .trading_decision import TradingDecisionMaker

try:
    from .remote_scanner import RemoteScannerClient
except ImportError:
    RemoteScannerClient = None  # type: ignore[assignment, misc]

LOGGER = logging.getLogger(__name__)


class DualOllamaEngine:
    """이중 Ollama 아키텍처 통합 엔진."""

    def __init__(
        self,
        scanner_model: str | None = None,
        decision_model: str | None = None,
        ollama_url: str | None = None,
        remote_scanner_url: str | None = None,  # 새 파라미터
        use_distributed: bool = False,  # 새 파라미터
        confidence_threshold: float = 0.6,
        high_risk: bool = False,
    ) -> None:
        """
        이중 Ollama 엔진 초기화.

        Args:
            scanner_model: 스캐너 모델 (기본: qwen2.5:1.5b)
            decision_model: 결정자 모델 (기본: qwen2.5-coder:7b)
            ollama_url: Ollama 서버 URL (None이면 기본값 사용)
            remote_scanner_url: 원격 스캐너 API URL (네트워크 모드용)
            use_distributed: 분산 모드 사용 여부 (기본값: False)
            confidence_threshold: 신뢰도 임계값
            high_risk: 고위험 모드 여부
        """
        self.use_distributed = use_distributed

        if use_distributed and remote_scanner_url:
            # 네트워크 모드: 원격 스캐너 사용
            if RemoteScannerClient is None:
                raise ImportError(
                    "원격 스캐너 클라이언트를 사용하려면 remote_scanner 모듈이 필요합니다."
                )

            max_age_seconds = int(os.getenv("SCAN_DATA_MAX_AGE_SECONDS", "120"))
            self.remote_scanner = RemoteScannerClient(
                api_url=remote_scanner_url, max_age_seconds=max_age_seconds
            )
            self.scanner = None  # 로컬 스캐너는 fallback용으로만
            LOGGER.info(
                f"네트워크 모드: 원격 스캐너 사용 ({remote_scanner_url}, "
                f"최대 나이: {max_age_seconds}초)"
            )
        else:
            # 서버 로컬 스캐너 사용 (기본 모드)
            url = ollama_url or OLLAMA_BASE_URL
            self.scanner = CoinScanner(ollama_url=url, model=scanner_model)
            self.remote_scanner = None
            LOGGER.info(f"서버 로컬 스캐너 사용 ({url}) - 모든 스캔 및 분석은 서버에서 처리됩니다")

        # Decision Maker는 항상 필요
        decision_url = os.getenv("OLLAMA_DECISION_URL", ollama_url or OLLAMA_BASE_URL)
        self.decision_maker = TradingDecisionMaker(
            ollama_url=decision_url,
            model=decision_model,
            confidence_threshold=confidence_threshold,
            high_risk=high_risk,
        )

        self.last_analysis: dict[str, Any] | None = None

    def analyze_markets(
        self,
        markets_data: dict[str, list[Candle]],
        current_portfolio: dict[str, Any],
        market_context: dict[str, Any],
    ) -> tuple[StrategySignal, str | None, float, dict[str, Any]]:
        """
        여러 코인을 분석하여 최종 매매 결정.

        Args:
            markets_data: {market: [candles]} 딕셔너리
            current_portfolio: 현재 포트폴리오 정보
            market_context: 전체 시장 상황

        Returns:
            (signal, selected_market, confidence, analysis_data)
        """
        LOGGER.info("=" * 60)
        LOGGER.info("이중 Ollama 분석 시작")
        LOGGER.info("=" * 60)

        if self.use_distributed and self.remote_scanner:
            return self._analyze_with_remote_scanner(
                markets_data, current_portfolio, market_context
            )
        else:
            return self._analyze_with_local_scanner(
                markets_data, current_portfolio, market_context
            )

    def _analyze_with_remote_scanner(
        self,
        markets_data: dict[str, list[Candle]],
        current_portfolio: dict[str, Any],
        market_context: dict[str, Any],
    ) -> tuple[StrategySignal, str | None, float, dict[str, Any]]:
        """원격 스캐너 사용 분석 (네트워크 모드)."""

        fallback_mode = os.getenv("FALLBACK_MODE", "auto")

        try:
            # 1단계: 원격 스캔 결과 가져오기
            LOGGER.info("Step 1: 원격 스캔 결과 가져오기")
            scan_results = self.remote_scanner.get_fresh_results()

            if not scan_results:
                raise ValueError("스캔 결과 없음")

            LOGGER.info(f"원격 스캔 결과: {len(scan_results)}개 코인")

            # 2단계: Decision Maker로 분석
            LOGGER.info("Step 2: 매매 결정 (Ollama 2 - 분석 및 판단)")
            signal, market, confidence, analysis = (
                self.decision_maker.analyze_from_remote_scan(
                    scan_results, current_portfolio, market_context
                )
            )

            # 결과 저장
            self.last_analysis = {
                "scan_results": scan_results,
                "decision": analysis,
                "signal": signal.value,
                "selected_market": market,
                "confidence": confidence,
                "mode": "remote",
            }

            LOGGER.info("=" * 60)
            LOGGER.info(
                f"이중 Ollama 분석 완료: {signal.value} {market or ''} "
                f"(신뢰도: {confidence:.2%}, 모드: 원격)"
            )
            LOGGER.info("=" * 60)

            return signal, market, confidence, self.last_analysis

        except Exception as e:
            LOGGER.error(f"원격 스캐너 오류: {e}")

            if fallback_mode == "remote_only":
                LOGGER.warning("Fallback 비활성화 - HOLD 반환")
                return StrategySignal.HOLD, None, 0.0, {}

            # 2단계: 로컬 캐시 확인
            if self.remote_scanner.cache:
                LOGGER.info("로컬 캐시 사용 (Fallback 2단계)")
                scan_results = self.remote_scanner.cache

                signal, market, confidence, analysis = (
                    self.decision_maker.analyze_from_remote_scan(
                        scan_results, current_portfolio, market_context
                    )
                )

                self.last_analysis = {
                    "scan_results": scan_results,
                    "decision": analysis,
                    "signal": signal.value,
                    "selected_market": market,
                    "confidence": confidence,
                    "mode": "cache_fallback",
                }

                return signal, market, confidence, self.last_analysis

            # 3단계: 로컬 스캔 (긴급)
            if fallback_mode == "auto":
                LOGGER.warning("로컬 스캔으로 Fallback (3단계)")
                return self._analyze_with_local_scanner(
                    markets_data, current_portfolio, market_context
                )

            return StrategySignal.HOLD, None, 0.0, {}

    def _analyze_with_local_scanner(
        self,
        markets_data: dict[str, list[Candle]],
        current_portfolio: dict[str, Any],
        market_context: dict[str, Any],
    ) -> tuple[StrategySignal, str | None, float, dict[str, Any]]:
        """서버 로컬 스캐너 사용 분석 (서버에서 모든 처리)."""

        # 기존 로직 유지
        if not self.scanner:
            # 긴급 생성 (fallback용)
            from .ollama_client import OLLAMA_BASE_URL

            self.scanner = CoinScanner(ollama_url=OLLAMA_BASE_URL)

        # Step 1: 1차 빠른 필터링 (기술적 지표만, Ollama 호출 없음)
        LOGGER.info("Step 1: 1차 빠른 필터링 (기술적 지표만, Ollama 없음)")
        
        # 최신 스캔 결과 확인 (캐시 우선 사용: 30분 이내면 재사용)
        from datetime import UTC, datetime

        last_scan_time = self.scanner.last_scan_time
        coin_analyses: dict[str, dict[str, Any]] = {}
        CACHE_MAX_AGE = 1800  # 30분 (1800초)
        
        if last_scan_time:
            time_diff = (datetime.now(UTC) - last_scan_time).total_seconds()
            if time_diff < CACHE_MAX_AGE:  # 30분 이내면 재사용
                coin_analyses = self.scanner.get_last_scan_result() or {}
                if coin_analyses:
                    LOGGER.info(
                        f"캐시된 스캔 결과 재사용 ({time_diff:.1f}초 전 스캔, {len(coin_analyses)}개 코인)"
                    )
                else:
                    # 스캔 결과가 없으면 빠른 필터링 실행
                    LOGGER.info("스캔 결과 없음, 빠른 필터링 시작")
                    try:
                        coin_analyses = self.scanner.fast_filter_by_indicators(markets_data, top_n=30)
                        # 결과 저장 (다음 분석에서 재사용)
                        self.scanner.last_scan_result = coin_analyses
                        self.scanner.last_scan_time = datetime.now(UTC)
                    except Exception as e:
                        LOGGER.error(f"빠른 필터링 실패: {e}, 캐시 확인")
                        # 스캔 실패 시 캐시 재확인 (더 오래된 것도 허용)
                        cached_result = self.scanner.get_last_scan_result()
                        if cached_result:
                            cache_age = (datetime.now(UTC) - last_scan_time).total_seconds()
                            if cache_age < CACHE_MAX_AGE * 2:  # 60분 이내 캐시 허용
                                LOGGER.warning(f"필터링 실패, 오래된 캐시 사용 ({cache_age:.0f}초 전)")
                                coin_analyses = cached_result
                            else:
                                LOGGER.warning("캐시도 너무 오래됨, 빈 결과 반환")
                                coin_analyses = {}
                        else:
                            coin_analyses = {}
            else:
                # 30분 이상 지났으면 새로 빠른 필터링
                LOGGER.info(f"스캔 결과가 오래됨 ({time_diff:.1f}초), 새로 빠른 필터링")
                try:
                    coin_analyses = self.scanner.fast_filter_by_indicators(markets_data, top_n=30)
                    # 결과 저장
                    self.scanner.last_scan_result = coin_analyses
                    self.scanner.last_scan_time = datetime.now(UTC)
                except Exception as e:
                    LOGGER.error(f"빠른 필터링 실패: {e}, 캐시 확인")
                    # 스캔 실패 시 캐시 재확인
                    cached_result = self.scanner.get_last_scan_result()
                    if cached_result:
                        cache_age = (datetime.now(UTC) - last_scan_time).total_seconds()
                        if cache_age < CACHE_MAX_AGE * 2:  # 60분 이내 캐시 허용
                            LOGGER.warning(f"필터링 실패, 오래된 캐시 사용 ({cache_age:.0f}초 전)")
                            coin_analyses = cached_result
                        else:
                            LOGGER.warning("캐시도 너무 오래됨, 빈 결과 반환")
                            coin_analyses = {}
                    else:
                        coin_analyses = {}
        else:
            # 스캔 결과가 없으면 새로 빠른 필터링
            LOGGER.info("최초 빠른 필터링 시작")
            try:
                coin_analyses = self.scanner.fast_filter_by_indicators(markets_data, top_n=30)
                # 결과 저장
                self.scanner.last_scan_result = coin_analyses
                self.scanner.last_scan_time = datetime.now(UTC)
            except Exception as e:
                LOGGER.error(f"빠른 필터링 실패: {e}, 빈 결과 반환")
                coin_analyses = {}

        if not coin_analyses:
            LOGGER.warning("코인 스캔 결과가 없어 HOLD 결정 (빈 분석 결과 반환)")
            return StrategySignal.HOLD, None, 0.0, {
                "coin_analyses": {},
                "decision": {"signal": "HOLD", "reason": "스캔 결과 없음"},
                "signal": "HOLD",
                "selected_market": None,
                "confidence": 0.0,
                "mode": "local",
            }

        LOGGER.info(f"코인 스캔 완료: {len(coin_analyses)}개 코인 분석됨")

        # Step 2: Ollama 2 - 매매 결정
        LOGGER.info("Step 2: 매매 결정 (Ollama 2 - 분석 및 판단)")
        try:
            signal, market, confidence, decision_data = self.decision_maker.make_decision(
                coin_analyses=coin_analyses,
                current_portfolio=current_portfolio,
                market_context=market_context,
            )
        except Exception as e:
            LOGGER.error(f"매매 결정 생성 실패: {e}, HOLD 반환")
            signal = StrategySignal.HOLD
            market = None
            confidence = 0.0
            decision_data = {
                "signal": "HOLD",
                "reason": f"매매 결정 생성 실패: {str(e)[:100]}",
                "confidence": 0.0,
            }

        # 결과 저장
        self.last_analysis = {
            "coin_analyses": coin_analyses,
            "decision": decision_data,
            "signal": signal.value,
            "selected_market": market,
            "confidence": confidence,
            "scanner_result": self.scanner.last_scan_result,
            "mode": "local",
        }

        LOGGER.info("=" * 60)
        LOGGER.info(
            f"이중 Ollama 분석 완료: {signal.value} {market or ''} "
            f"(신뢰도: {confidence:.2%}, 모드: 로컬)"
        )
        LOGGER.info("=" * 60)

        return signal, market, confidence, self.last_analysis

