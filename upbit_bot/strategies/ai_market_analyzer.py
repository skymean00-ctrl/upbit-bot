"""AI-powered market analysis strategy using local Ollama."""

from __future__ import annotations

import json
import logging
import statistics
from collections.abc import Iterable
from typing import Any

import requests

from .base import Candle, Strategy, StrategySignal

LOGGER = logging.getLogger(__name__)

# Ollama 설정
OLLAMA_BASE_URL = "http://100.98.189.30:11434"
OLLAMA_MODEL = "qwen2.5-coder:7b"  # 빠르고 가벼운 모델


class AIMarketAnalyzer(Strategy):
    """AI 기반 실시간 시장 분석 전략."""

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
        self._verify_ollama_connection()

    def _verify_ollama_connection(self) -> None:
        """Ollama 서버 연결 확인."""
        try:
            response = requests.get(
                f"{OLLAMA_BASE_URL}/api/tags",
                timeout=5,
            )
            if response.status_code == 200:
                models = response.json().get("models", [])
                LOGGER.info(f"Ollama 연결 성공. 사용 가능한 모델: {len(models)}개")
            else:
                LOGGER.warning(
                    f"Ollama 응답 오류: {response.status_code}"
                )
        except requests.exceptions.RequestException as e:
            LOGGER.error(f"Ollama 연결 실패: {e}")

    def _calculate_technical_indicators(self, candles: list[Candle]) -> dict[str, Any]:
        """기술적 지표 계산."""
        if len(candles) < 20:
            return {}

        closes = [c.close for c in candles]
        volumes = [c.volume for c in candles]

        # 이동평균선
        ma_5 = statistics.mean(closes[-5:])
        ma_10 = statistics.mean(closes[-10:])
        ma_20 = statistics.mean(closes[-20:])

        # 변동성 (표준편차)
        volatility = statistics.stdev(closes[-20:]) / ma_20 * 100

        # 최근 가격 변화
        recent_change = ((closes[-1] - closes[-5]) / closes[-5]) * 100

        # 평균 거래량
        avg_volume = statistics.mean(volumes[-10:])
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

    def _query_ollama(self, market_data: dict[str, Any]) -> tuple[StrategySignal, float]:
        """
        Ollama AI에 시장 분석 요청.

        Returns:
            (신호, 신뢰도)
        """
        prompt = f"""당신은 암호화폐 거래 전문가입니다.
다음 시장 데이터를 분석하고 매매 신호를 제시하세요.

시장 데이터:
- 현재 가격: {market_data['current_price']:,.0f} 원
- 5분 이동평균: {market_data['ma_5']:,.0f}
- 10분 이동평균: {market_data['ma_10']:,.0f}
- 20분 이동평균: {market_data['ma_20']:,.0f}
- 변동성: {market_data['volatility']:.2f}%
- 최근 5분 변화: {market_data['recent_change']:+.2f}%
- 거래량 비율: {market_data['volume_ratio']:.2f}x
- 트렌드: {market_data['trend']}

다음 중 하나를 선택하세요:
1. BUY - 강한 상승 신호
2. SELL - 강한 하락 신호  
3. HOLD - 신호 없음

선택 이유와 신뢰도(0.0~1.0)를 JSON으로 응답하세요:
{{"signal": "BUY|SELL|HOLD", "confidence": 0.0~1.0, "reason": "이유"}}"""

        try:
            response = requests.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "temperature": 0.3,  # 낮은 온도 = 더 결정적
                },
                timeout=45,  # 1분 주기 내에 충분한 시간 확보
            )

            if response.status_code != 200:
                LOGGER.warning(f"Ollama 오류: {response.status_code}")
                return StrategySignal.HOLD, 0.0

            result = response.json()
            response_text = result.get("response", "")

            # JSON 파싱 시도
            try:
                # JSON 부분 추출
                json_start = response_text.find("{")
                json_end = response_text.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    json_str = response_text[json_start:json_end]
                    data = json.loads(json_str)

                    signal_str = data.get("signal", "HOLD").upper()
                    confidence = float(data.get("confidence", 0.0))

                    # 신뢰도에 따라 신호 결정
                    if confidence < self.confidence_threshold:
                        return StrategySignal.HOLD, confidence

                    if signal_str == "BUY":
                        return StrategySignal.BUY, confidence
                    elif signal_str == "SELL":
                        return StrategySignal.SELL, confidence

                    return StrategySignal.HOLD, confidence
            except (json.JSONDecodeError, ValueError) as e:
                LOGGER.warning(f"응답 파싱 실패: {e}")
                # 응답 텍스트에서 신호 검색
                if "buy" in response_text.lower():
                    return StrategySignal.BUY, 0.5
                elif "sell" in response_text.lower():
                    return StrategySignal.SELL, 0.5

            return StrategySignal.HOLD, 0.0

        except requests.exceptions.RequestException as e:
            LOGGER.error(f"Ollama 요청 실패: {e}")
            return StrategySignal.HOLD, 0.0

    def on_candles(self, candles: Iterable[Candle]) -> StrategySignal:
        """생성된 캔들을 기반으로 신호 생성."""
        candles_list = list(candles)
        if len(candles_list) < 5:
            return StrategySignal.HOLD

        # 기술적 지표 계산
        market_data = self._calculate_technical_indicators(candles_list)
        if not market_data:
            return StrategySignal.HOLD

        # AI 분석
        signal, confidence = self._query_ollama(market_data)

        # 결과 저장 (로깅용)
        self.last_analysis = {
            "market_data": market_data,
            "signal": signal,
            "confidence": confidence,
        }

        LOGGER.info(
            f"AI 분석: {signal.value} (신뢰도: {confidence:.2%})"
        )

        self.last_signal = signal
        return signal

