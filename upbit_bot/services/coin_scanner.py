"""Ollama 1: 코인 스캐너 (정보 수집용 - 1.5b 모델)."""

from __future__ import annotations

import logging
import statistics
from typing import Any

from upbit_bot.strategies import Candle

from .ollama_client import OllamaClient, OllamaError, OLLAMA_BASE_URL, OLLAMA_SCANNER_MODEL

LOGGER = logging.getLogger(__name__)


class CoinScanner:
    """여러 코인을 스캔하고 정보를 수집하는 Ollama 인스턴스."""

    def __init__(
        self,
        ollama_url: str = OLLAMA_BASE_URL,
        model: str = OLLAMA_SCANNER_MODEL,
        timeout: int = 30,
    ) -> None:
        self.client = OllamaClient(base_url=ollama_url, model=model, timeout=timeout)
        self.last_scan_result: dict[str, dict[str, Any]] | None = None

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
        여러 코인을 스캔하여 정보 수집.

        Args:
            markets_data: {market: [candles]} 딕셔너리
            max_workers: 최대 동시 처리 수 (현재는 순차 처리, 향후 병렬 처리 가능)

        Returns:
            {market: {score, reason, trend, risk, indicators}} 딕셔너리
        """
        results: dict[str, dict[str, Any]] = {}

        LOGGER.info(f"코인 스캔 시작: {len(markets_data)}개 코인")
        for idx, (market, candles) in enumerate(markets_data.items(), 1):
            LOGGER.info(f"[{idx}/{len(markets_data)}] 스캔 중: {market}")
            result = self.scan_single_market(market, candles)
            if result:
                results[market] = result
                LOGGER.debug(
                    f"  {market}: score={result['score']:.2f}, "
                    f"trend={result['trend']}, risk={result['risk']}"
                )

        self.last_scan_result = results
        LOGGER.info(f"코인 스캔 완료: {len(results)}개 코인 분석됨")
        return results

