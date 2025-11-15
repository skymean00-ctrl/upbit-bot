"""이중 Ollama 엔진: CoinScanner + TradingDecisionMaker 통합."""

from __future__ import annotations

import logging
from typing import Any

from upbit_bot.strategies import Candle, StrategySignal

from .coin_scanner import CoinScanner
from .ollama_client import OLLAMA_BASE_URL
from .trading_decision import TradingDecisionMaker

LOGGER = logging.getLogger(__name__)


class DualOllamaEngine:
    """이중 Ollama 아키텍처 통합 엔진."""

    def __init__(
        self,
        scanner_model: str | None = None,
        decision_model: str | None = None,
        ollama_url: str | None = None,
        confidence_threshold: float = 0.6,
        high_risk: bool = False,
    ) -> None:
        """
        이중 Ollama 엔진 초기화.

        Args:
            scanner_model: 스캐너 모델 (기본: qwen2.5:1.5b)
            decision_model: 결정자 모델 (기본: qwen2.5-coder:7b)
            ollama_url: Ollama 서버 URL (None이면 기본값 사용)
            confidence_threshold: 신뢰도 임계값
            high_risk: 고위험 모드 여부
        """
        # ollama_url이 None이면 기본값 사용
        url = ollama_url or OLLAMA_BASE_URL
        self.scanner = CoinScanner(ollama_url=url, model=scanner_model)
        self.decision_maker = TradingDecisionMaker(
            ollama_url=url,
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

        # Step 1: Ollama 1 - 코인 스캔 (최신 스캔 결과 재사용 또는 새로 스캔)
        LOGGER.info("Step 1: 코인 스캔 (Ollama 1 - 정보 수집)")
        
        # 최신 스캔 결과 확인 (60초 이내면 재사용)
        from datetime import datetime, UTC
        last_scan_time = self.scanner.last_scan_time
        if last_scan_time:
            time_diff = (datetime.now(UTC) - last_scan_time).total_seconds()
            if time_diff < 60:  # 60초 이내면 재사용
                coin_analyses = self.scanner.get_last_scan_result()
                if coin_analyses:
                    LOGGER.info(f"최신 스캔 결과 재사용 ({time_diff:.1f}초 전 스캔)")
                else:
                    # 스캔 결과가 없으면 새로 스캔
                    coin_analyses = self.scanner.scan_markets(markets_data)
            else:
                # 60초 이상 지났으면 새로 스캔
                LOGGER.info(f"스캔 결과가 오래됨 ({time_diff:.1f}초), 새로 스캔")
                coin_analyses = self.scanner.scan_markets(markets_data)
        else:
            # 스캔 결과가 없으면 새로 스캔
            coin_analyses = self.scanner.scan_markets(markets_data)

        if not coin_analyses:
            LOGGER.warning("코인 스캔 결과가 없어 HOLD 결정")
            return StrategySignal.HOLD, None, 0.0, {}

        LOGGER.info(f"코인 스캔 완료: {len(coin_analyses)}개 코인 분석됨")

        # Step 2: Ollama 2 - 매매 결정
        LOGGER.info("Step 2: 매매 결정 (Ollama 2 - 분석 및 판단)")
        signal, market, confidence, decision_data = self.decision_maker.make_decision(
            coin_analyses=coin_analyses,
            current_portfolio=current_portfolio,
            market_context=market_context,
        )

        # 결과 저장
        self.last_analysis = {
            "coin_analyses": coin_analyses,
            "decision": decision_data,
            "signal": signal.value,
            "selected_market": market,
            "confidence": confidence,
            "scanner_result": self.scanner.last_scan_result,
        }

        LOGGER.info("=" * 60)
        LOGGER.info(f"이중 Ollama 분석 완료: {signal.value} {market or ''} (신뢰도: {confidence:.2%})")
        LOGGER.info("=" * 60)

        return signal, market, confidence, self.last_analysis

