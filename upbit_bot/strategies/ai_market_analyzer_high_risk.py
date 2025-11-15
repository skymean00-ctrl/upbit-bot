"""High-risk AI-powered market analysis strategy using dual Ollama architecture."""

from __future__ import annotations

import logging

from .ai_market_analyzer import AIMarketAnalyzer

LOGGER = logging.getLogger(__name__)


class AIMarketAnalyzerHighRisk(AIMarketAnalyzer):
    """고위험 고수익 AI 기반 실시간 시장 분석 전략 (이중 Ollama 아키텍처).
    
    기본 AI 시장 분석 전략을 베이스로 하되, 더 공격적인 매매를 위해:
    - 낮은 신뢰도 임계값 (기본 0.6 -> 0.4)
    - 빠른 진입/퇴출
    - 공격적 프롬프트 사용
    """

    name = "ai_market_analyzer_high_risk"

    def __init__(self, confidence_threshold: float = 0.4) -> None:
        """
        Initialize High-Risk AI Market Analyzer.

        Args:
            confidence_threshold: AI 신뢰도 임계값 (기본값: 0.4, 기본 전략은 0.6)
                                낮을수록 더 많은 매매 신호 생성 (하이리스크 하이리턴)
        """
        # 부모 클래스 초기화 (낮은 임계값으로)
        super().__init__(confidence_threshold=confidence_threshold)
        # 고위험 모드 설정
        self._high_risk = True

    def _get_dual_engine(self):
        """Lazy initialization of dual engine (고위험 모드)."""
        if self.dual_engine is None:
            from upbit_bot.services.dual_ollama_engine import DualOllamaEngine
            
            self.dual_engine = DualOllamaEngine(
                confidence_threshold=self.confidence_threshold,
                high_risk=True,
            )
            
            # Ollama 연결 확인
            if self.dual_engine.scanner.client.verify_connection():
                LOGGER.info("Ollama 스캐너 연결 확인됨 (고위험 모드)")
            else:
                LOGGER.warning("Ollama 스캐너 연결 실패")
                
            if self.dual_engine.decision_maker.client.verify_connection():
                LOGGER.info("Ollama 결정자 연결 확인됨 (고위험 모드)")
            else:
                LOGGER.warning("Ollama 결정자 연결 실패")
        
        return self.dual_engine

