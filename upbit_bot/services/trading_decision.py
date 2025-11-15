"""Ollama 2: 매매 결정자 (분석 및 판단용 - 7b 모델)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from upbit_bot.strategies import StrategySignal

from .ollama_client import OllamaClient, OllamaError, OLLAMA_BASE_URL, OLLAMA_DECISION_MODEL

LOGGER = logging.getLogger(__name__)


class TradingDecisionMaker:
    """Ollama 1의 분석 결과를 바탕으로 최종 매매 결정을 내리는 Ollama 인스턴스."""

    def __init__(
        self,
        ollama_url: str | None = None,
        model: str | None = None,
        timeout: int = 45,
        confidence_threshold: float = 0.6,
        high_risk: bool = False,
    ) -> None:
        # ollama_url과 model이 None이면 기본값 사용
        url = ollama_url or OLLAMA_BASE_URL
        model_name = model or OLLAMA_DECISION_MODEL
        self.client = OllamaClient(base_url=url, model=model_name, timeout=timeout)
        self.confidence_threshold = confidence_threshold
        self.high_risk = high_risk
        self.last_decision: dict[str, Any] | None = None

    def make_decision(
        self,
        coin_analyses: dict[str, dict[str, Any]],
        current_portfolio: dict[str, Any],
        market_context: dict[str, Any],
    ) -> tuple[StrategySignal, str | None, float, dict[str, Any]]:
        """
        여러 코인 분석 결과를 종합하여 최종 매매 결정.

        Args:
            coin_analyses: Ollama 1의 스캔 결과 {market: {score, reason, trend, risk, indicators}}
            current_portfolio: 현재 포트폴리오 정보
            market_context: 전체 시장 상황

        Returns:
            (signal, selected_market, confidence, decision_data)
        """
        if not coin_analyses:
            LOGGER.warning("분석 결과가 없어 HOLD 결정")
            return StrategySignal.HOLD, None, 0.0, {}

        # 점수 기준으로 정렬
        sorted_analyses = sorted(
            coin_analyses.items(), key=lambda x: x[1].get("score", 0.0), reverse=True
        )
        top_10 = sorted_analyses[:10]  # 상위 10개 선택

        prompt = f"""당신은 암호화폐 거래 전문가입니다.
다음 정보를 종합하여 최종 매매 결정을 내리세요.

[현재 시간]
{datetime.now(UTC).isoformat()}

[코인 분석 결과 (Ollama 1 스캔 - 상위 10개)]
{self._format_coin_analyses(dict(top_10))}

[현재 포트폴리오]
{self._format_portfolio(current_portfolio)}

[시장 상황]
{self._format_market_context(market_context)}

[제약 조건]
- 최대 포지션 수: {market_context.get('max_positions', 5)}개
- 최소 주문 금액: {market_context.get('min_order_amount', 5000):,.0f}원
- 현재 포지션 수: {len(current_portfolio.get('open_positions', []))}개

{f'[고위험 모드]\n공격적 매매 원칙: 작은 신호라도 포착, 변동성이 높을수록 기회로 간주, 빠른 진입/퇴출' if self.high_risk else ''}

다음 중 하나를 선택하세요:
1. BUY [코인명] - 가장 유망한 코인 매수
2. SELL [코인명] - 보유 중인 코인 매도
3. HOLD - 현재 상태 유지

다음 JSON 형식으로 응답하세요:
{{
  "signal": "BUY|SELL|HOLD",
  "market": "KRW-XXX",
  "confidence": 0.0~1.0,
  "reason": "상세한 이유",
  "alternative_options": [
    {{"market": "KRW-YYY", "score": 0.75, "reason": "대안 선택 이유"}}
  ],
  "risk_assessment": {{
    "level": "low|medium|high",
    "factors": ["요소1", "요소2"]
  }}
}}

판단 기준:
1. 코인 점수 (Ollama 1 분석)
2. 현재 포트폴리오 분산
3. 리스크 수준
4. 시장 트렌드
5. 거래량 및 유동성"""

        try:
            # 고위험 모드는 더 높은 온도로 다양성 증가
            temperature = 0.5 if self.high_risk else 0.3
            response_text = self.client.generate(prompt, temperature=temperature)
            data = self.client.parse_json_response(response_text)

            signal_str = data.get("signal", "HOLD").upper()
            market = data.get("market")
            confidence = float(data.get("confidence", 0.0))

            # 신뢰도 검증
            if confidence < self.confidence_threshold:
                LOGGER.info(
                    f"신뢰도 낮음 ({confidence:.2%} < {self.confidence_threshold:.2%}), HOLD 결정"
                )
                signal_str = "HOLD"

            # 신호 결정
            if signal_str == "BUY":
                signal = StrategySignal.BUY
            elif signal_str == "SELL":
                signal = StrategySignal.SELL
            else:
                signal = StrategySignal.HOLD

            decision_data = {
                "signal": signal_str,
                "market": market,
                "confidence": confidence,
                "reason": data.get("reason", ""),
                "alternatives": data.get("alternative_options", []),
                "risk": data.get("risk_assessment", {}),
                "timestamp": datetime.now(UTC).isoformat(),
            }

            self.last_decision = decision_data

            LOGGER.info(
                f"매매 결정: {signal_str} {market or ''} (신뢰도: {confidence:.2%})"
            )

            return signal, market, confidence, decision_data

        except OllamaError as e:
            LOGGER.error(f"매매 결정 생성 실패: {e}")
            return StrategySignal.HOLD, None, 0.0, {}

    def _format_coin_analyses(self, analyses: dict[str, dict[str, Any]]) -> str:
        """코인 분석 결과를 텍스트로 포맷."""
        lines = []
        for market, analysis in analyses.items():
            lines.append(f"- {market}:")
            lines.append(f"  점수: {analysis.get('score', 0.0):.2f}")
            lines.append(f"  이유: {analysis.get('reason', '')}")
            lines.append(f"  트렌드: {analysis.get('trend', '')}")
            lines.append(f"  리스크: {analysis.get('risk', 'medium')}")
        return "\n".join(lines)

    def _format_portfolio(self, portfolio: dict[str, Any]) -> str:
        """포트폴리오 정보를 텍스트로 포맷."""
        lines = []
        lines.append(f"총 잔고: {portfolio.get('total_balance', 0):,.0f}원")
        lines.append(f"KRW 잔고: {portfolio.get('krw_balance', 0):,.0f}원")
        lines.append(f"보유 포지션 수: {len(portfolio.get('open_positions', []))}개")

        positions = portfolio.get("open_positions", [])
        if positions:
            lines.append("보유 포지션:")
            for pos in positions[:5]:  # 최대 5개만 표시
                market = pos.get("market", "")
                amount = pos.get("purchase_amount", 0)
                pnl = pos.get("crypto_value", 0) - amount
                lines.append(f"  - {market}: {amount:,.0f}원 (수익/손실: {pnl:+,.0f}원)")

        return "\n".join(lines)

    def _format_market_context(self, context: dict[str, Any]) -> str:
        """시장 상황을 텍스트로 포맷."""
        lines = []
        lines.append(f"총 잔고: {context.get('total_balance', 0):,.0f}원")
        lines.append(f"최대 포지션 수: {context.get('max_positions', 5)}개")
        lines.append(f"현재 포지션 수: {context.get('current_positions', 0)}개")
        lines.append(f"리스크 레벨: {context.get('risk_level', 'medium')}")
        lines.append(f"시장 트렌드: {context.get('market_trend', 'unknown')}")
        return "\n".join(lines)

