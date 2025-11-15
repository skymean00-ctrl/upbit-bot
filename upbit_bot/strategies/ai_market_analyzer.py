"""AI-powered market analysis strategy using dual Ollama architecture."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from .base import Candle, Strategy, StrategySignal

LOGGER = logging.getLogger(__name__)


class AIMarketAnalyzer(Strategy):
    """AI 기반 실시간 시장 분석 전략 (이중 Ollama 아키텍처)."""

    name = "ai_market_analyzer"

    def __init__(self, confidence_threshold: float = 0.6) -> None:
        """
        Initialize AI Market Analyzer.

        Args:
            confidence_threshold: AI 신뢰도 임계값 (0.0 ~ 1.0)
                                낮을수록 더 많은 신호 생성
        """
        self.confidence_threshold = confidence_threshold
        self.last_signal = StrategySignal.HOLD
        self.last_analysis = None
        # Lazy import to avoid circular dependency
        self.dual_engine = None

    def _get_dual_engine(self):
        """Lazy initialization of dual engine."""
        if self.dual_engine is None:
            from upbit_bot.services.dual_ollama_engine import DualOllamaEngine
            
            self.dual_engine = DualOllamaEngine(
                confidence_threshold=self.confidence_threshold,
                high_risk=False,
            )
            
            # Ollama 연결 확인
            if self.dual_engine.scanner.client.verify_connection():
                LOGGER.info("Ollama 스캐너 연결 확인됨")
            else:
                LOGGER.warning("Ollama 스캐너 연결 실패")
                
            if self.dual_engine.decision_maker.client.verify_connection():
                LOGGER.info("Ollama 결정자 연결 확인됨")
            else:
                LOGGER.warning("Ollama 결정자 연결 실패")
        
        return self.dual_engine

    def on_candles(self, candles: Iterable[Candle]) -> StrategySignal:
        """생성된 캔들을 기반으로 신호 생성 (단일 코인용 - 호환성 유지)."""
        candles_list = list(candles)
        if len(candles_list) < 5:
            self.last_analysis = {
                "signal": StrategySignal.HOLD,
                "confidence": 0.0,
                "status": "insufficient_data",
            }
            return StrategySignal.HOLD

        # 단일 코인 모드에서는 HOLD 반환
        # 실제 분석은 ExecutionEngine._analyze_multiple_markets()에서 이중 Ollama로 수행됨
        self.last_analysis = {
            "signal": StrategySignal.HOLD,
            "confidence": 0.0,
            "status": "single_coin_mode",
            "note": "이중 Ollama 분석은 ExecutionEngine._analyze_multiple_markets()에서 수행됩니다",
        }

        return StrategySignal.HOLD

