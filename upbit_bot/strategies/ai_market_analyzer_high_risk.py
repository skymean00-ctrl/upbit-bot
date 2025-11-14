"""High-risk AI-powered market analysis strategy using local Ollama."""

from __future__ import annotations

from .ai_market_analyzer import AIMarketAnalyzer


class AIMarketAnalyzerHighRisk(AIMarketAnalyzer):
    """고위험 고수익 AI 기반 실시간 시장 분석 전략.
    
    기본 AI 시장 분석 전략을 베이스로 하되, 더 공격적인 매매를 위해:
    - 낮은 신뢰도 임계값 (기본 0.6 -> 0.4)
    - 더 높은 온도 (더 다양한 신호 생성)
    - 빠른 진입/퇴출
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
    
    def _query_ollama(self, market_data: dict) -> tuple:
        """
        Ollama AI에 시장 분석 요청 (고위험 모드).
        
        기본 전략보다 더 공격적인 프롬프트와 높은 온도 사용.
        """
        prompt = f"""당신은 공격적인 암호화폐 거래 전문가입니다.
다음 시장 데이터를 분석하고 빠른 매매 신호를 제시하세요.
고위험 고수익 전략을 사용합니다 - 신호가 약해도 기회를 포착하세요.

시장 데이터:
- 현재 가격: {market_data['current_price']:,.0f} 원
- 5분 이동평균: {market_data['ma_5']:,.0f}
- 10분 이동평균: {market_data['ma_10']:,.0f}
- 20분 이동평균: {market_data['ma_20']:,.0f}
- 변동성: {market_data['volatility']:.2f}%
- 최근 5분 변화: {market_data['recent_change']:+.2f}%
- 거래량 비율: {market_data['volume_ratio']:.2f}x
- 트렌드: {market_data['trend']}

공격적 매매 원칙:
- 작은 신호라도 포착 (기본 전략보다 빠른 진입)
- 변동성이 높을수록 기회로 간주
- 거래량 급증 시 즉시 대응
- 단기 수익 추구

다음 중 하나를 선택하세요:
1. BUY - 상승 신호 (약한 신호도 포함)
2. SELL - 하락 신호 (약한 신호도 포함)
3. HOLD - 명백히 신호 없음

선택 이유와 신뢰도(0.0~1.0)를 JSON으로 응답하세요:
{{"signal": "BUY|SELL|HOLD", "confidence": 0.0~1.0, "reason": "이유"}}"""

        try:
            from .ai_market_analyzer import OLLAMA_BASE_URL, OLLAMA_MODEL
            import requests
            import json
            from .base import StrategySignal
            
            response = requests.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "temperature": 0.5,  # 기본 0.3 -> 0.5 (더 다양한 응답)
                },
                timeout=45,
            )

            if response.status_code != 200:
                return StrategySignal.HOLD, 0.0

            result = response.json()
            response_text = result.get("response", "")
            
            if not response_text:
                return StrategySignal.HOLD, 0.0

            # JSON 파싱 시도
            try:
                json_start = response_text.find("{")
                json_end = response_text.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    json_str = response_text[json_start:json_end]
                    data = json.loads(json_str)

                    signal_str = data.get("signal", "HOLD").upper()
                    confidence = float(data.get("confidence", 0.0))

                    # 낮은 임계값으로 더 많은 신호 허용
                    if confidence < self.confidence_threshold:
                        return StrategySignal.HOLD, confidence

                    if signal_str == "BUY":
                        return StrategySignal.BUY, confidence
                    elif signal_str == "SELL":
                        return StrategySignal.SELL, confidence

                    return StrategySignal.HOLD, confidence
            except (json.JSONDecodeError, ValueError):
                # 응답 텍스트에서 신호 검색 (더 공격적으로)
                response_lower = response_text.lower()
                if "buy" in response_lower or "매수" in response_text:
                    return StrategySignal.BUY, 0.5
                elif "sell" in response_lower or "매도" in response_text:
                    return StrategySignal.SELL, 0.5

            return StrategySignal.HOLD, 0.0

        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f"High-risk AI 분석 실패: {e}")
            return StrategySignal.HOLD, 0.0

