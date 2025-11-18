"""Ollama 2: 매매 결정자 (분석 및 판단용 - 경량 모델)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from upbit_bot.strategies import StrategySignal

from .ollama_client import (
    OllamaClient,
    OllamaError,
    OLLAMA_BASE_URL,
    OLLAMA_DECISION_MODEL,
    OLLAMA_FIRST_ROUND_MODEL,
    OLLAMA_SECOND_ROUND_MODEL,
)

LOGGER = logging.getLogger(__name__)


class TradingDecisionMaker:
    """Ollama 1의 분석 결과를 바탕으로 최종 매매 결정을 내리는 Ollama 인스턴스."""

    def __init__(
        self,
        ollama_url: str | None = None,
        model: str | None = None,
        first_round_model: str | None = None,
        second_round_model: str | None = None,
        decision_model: str | None = None,
        timeout: int = 120,  # 타임아웃 120초로 증가 (Ollama 응답 지연 대응)
        confidence_threshold: float = 0.6,
        high_risk: bool = False,
    ) -> None:
        """
        TradingDecisionMaker 초기화.

        Args:
            ollama_url: Ollama 서버 URL
            model: 레거시 호환용 모델 (기본 결정자 모델)
            first_round_model: 1차 분석 모델 (거래량 상위 30개 스캔)
            second_round_model: 2차 분석 모델 (상위 10개 선정)
            decision_model: 최종 결정 모델 (상위 5개 중 선택)
            timeout: 타임아웃 (초)
            confidence_threshold: 신뢰도 임계값
            high_risk: 고위험 모드 여부
        """
        import os
        
        # ollama_url과 model이 None이면 기본값 사용
        url = ollama_url or OLLAMA_BASE_URL
        
        # 업무별 모델 설정 (환경 변수 우선, 없으면 기본값)
        self.first_round_model = (
            first_round_model
            or os.getenv("OLLAMA_FIRST_ROUND_MODEL")
            or OLLAMA_FIRST_ROUND_MODEL
        )
        self.second_round_model = (
            second_round_model
            or os.getenv("OLLAMA_SECOND_ROUND_MODEL")
            or OLLAMA_SECOND_ROUND_MODEL
        )
        self.decision_model = (
            decision_model
            or model
            or os.getenv("OLLAMA_DECISION_MODEL")
            or OLLAMA_DECISION_MODEL
        )
        
        # 각 단계별 클라이언트 생성
        self.first_round_client = OllamaClient(
            base_url=url, model=self.first_round_model, timeout=timeout
        )
        self.second_round_client = OllamaClient(
            base_url=url, model=self.second_round_model, timeout=timeout
        )
        self.decision_client = OllamaClient(
            base_url=url, model=self.decision_model, timeout=timeout
        )
        
        # 레거시 호환을 위한 기본 클라이언트 (최종 결정 모델 사용)
        self.client = self.decision_client
        
        # 전략별 신뢰도 임계값 설정
        if high_risk:
            # 고위험 전략: 낮은 진입장벽 (0.5 이상), 공격적 진입
            self.confidence_threshold = min(confidence_threshold, 0.5)  # 최대 0.5
            self.entry_barrier = "low"  # 낮은 진입장벽
        else:
            # 저위험 전략: 높은 진입장벽 (0.7 이상), 보수적 진입
            self.confidence_threshold = max(confidence_threshold, 0.7)  # 최소 0.7
            self.entry_barrier = "high"  # 높은 진입장벽
        
        self.high_risk = high_risk
        self.last_decision: dict[str, Any] | None = None
        
        LOGGER.info(
            f"TradingDecisionMaker 초기화 완료 ({'고위험' if high_risk else '저위험'}): "
            f"1차={self.first_round_model}, "
            f"2차={self.second_round_model}, "
            f"최종={self.decision_model}, "
            f"신뢰도임계값={self.confidence_threshold:.2f}, "
            f"진입장벽={self.entry_barrier}"
        )

    def select_top_candidates(
        self,
        scan_results: list[dict[str, Any]],
        current_positions: list[str],
        max_positions: int = 10,
        portfolio_info: dict[str, Any] | None = None,
        market_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        스캔 결과에서 상위 10개 선정.

        선정 기준:
        1. 보유 중인 코인도 포함 (추가 매수 판단을 위해)
        2. 고위험 제외 (일반 모드인 경우)
        3. 최소 점수 필터링 (0.6 이상)
        4. 거래량 기준 정렬 (유동성)
        5. 상위 20개에서 점수 기준 재정렬
        6. 최종 10개 선정

        Args:
            scan_results: 스캔 결과 리스트
            current_positions: 현재 보유 중인 코인 리스트
            max_positions: 최대 선택 개수 (기본값: 10)

        Returns:
            선정된 코인 리스트 (점수 내림차순, 보유 코인 포함)
        """
        # 1. 보유 중인 코인도 포함 (추가 매수 판단을 위해)
        # 보유 중인 코인은 별도 플래그 추가
        available = []
        held_count = 0
        for r in scan_results:
            market = r.get("market", "")
            is_held = market in current_positions
            if is_held:
                held_count += 1
                # 보유 중인 코인은 추가 매수 가능 여부를 판단하기 위해 포함
                r["is_held"] = True
            else:
                r["is_held"] = False
            available.append(r)

        LOGGER.debug(
            f"보유 코인 포함: {len(scan_results)} → {len(available)}개 "
            f"(보유: {held_count}개, 신규: {len(available) - held_count}개)"
        )

        # 2. 리스크 필터링 (전략별 차별화)
        if self.high_risk:
            # 고위험 전략: 모든 리스크 수준 허용 (high risk도 포함)
            LOGGER.debug(f"고위험 전략: 모든 리스크 수준 허용")
        else:
            # 저위험 전략: high risk 제외, medium/low만 허용
            available = [r for r in available if r.get("risk", "").lower() != "high"]
            LOGGER.debug(f"저위험 전략: 고위험 제외, {len(available)}개 남음")

        # 3. 최소 점수 필터링
        available = [
            r for r in available if float(r.get("score", 0)) >= self.confidence_threshold
        ]

        LOGGER.debug(f"최소 점수 필터링 ({self.confidence_threshold}): {len(available)}개 남음")

        # 4. 거래량 기준 정렬 (유동성)
        available.sort(key=lambda x: float(x.get("volume_24h", 0)), reverse=True)

        # 5. 상위 20개에서 점수 기준 재정렬
        top_20_by_volume = available[:20]
        top_20_by_volume.sort(key=lambda x: float(x.get("score", 0)), reverse=True)

        # 6. 최종 max_positions개 선정 (기본 10개, 이 중에서 실제 매매는 별도 규칙으로 결정)
        final = top_20_by_volume[:max_positions]

        # 7. 포트폴리오 내 기존 보유 비중 계산 (exposure)
        exposure_map: dict[str, float] = {}
        total_balance = 0.0
        try:
            if portfolio_info and isinstance(portfolio_info, dict):
                open_positions = portfolio_info.get("open_positions", []) or []
                # total_balance는 market_context에서 우선 사용, 없으면 포지션 current_value 합으로 계산
                if market_context and isinstance(market_context, dict):
                    total_balance = float(market_context.get("total_balance") or 0.0)
                if not total_balance:
                    total_balance = sum(
                        float(p.get("current_value", 0.0)) for p in open_positions
                    )
                if total_balance > 0:
                    for p in open_positions:
                        m = p.get("market")
                        if not m:
                            continue
                        value = float(p.get("current_value", 0.0))
                        if value <= 0:
                            continue
                        exposure_map[m] = value / total_balance
        except Exception as e:  # noqa: BLE001
            LOGGER.warning(f"포트폴리오 노출 비율 계산 실패: {e}")

        # 8. 다양성/분산을 위한 점수 페널티(효과 점수) 적용
        #    - 어떤 코인이 전체 포트폴리오의 40% 이상이면 추가 진입 시 강한 페널티
        #    - BTC 페널티 제거 (모든 코인 동일 평가)
        for c in final:
            market = c.get("market", "")
            base_score = float(c.get("score", 0.0))
            score_eff = base_score

            # 동일 종목 비중 상한 페널티 (예: 40% 이상이면 강하게, 25% 이상이면 약하게)
            exposure = exposure_map.get(market, 0.0)
            if exposure >= 0.4:
                # 이미 전체의 40% 이상을 차지 → 사실상 추가 진입을 피하고 싶으므로 강한 페널티
                score_eff *= 0.5
            elif exposure >= 0.25:
                # 25% 이상 ~ 40% 미만 → 약한 페널티
                score_eff *= 0.8

            c["base_score"] = base_score
            c["score_eff"] = score_eff
            if exposure > 0:
                # 디버깅/로그용 노출 비율
                c["exposure_pct"] = exposure * 100.0

        LOGGER.info(
            f"후보 선정: {len(scan_results)} → {len(available)} → {len(final)}개 "
            f"(최종 선정 기준: 거래량 상위 20개 중 점수 상위 {max_positions}개, 포트폴리오 비중 페널티 적용)"
        )

        return final

    def analyze_second_round(
        self,
        candidates: list[dict[str, Any]],
        current_portfolio: dict[str, Any],
        market_context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        2차 선정: Ollama로 30개 중 10개로 줄이는 과정에서 AI 사용.
        
        Args:
            candidates: 1차 선정된 후보들 (기술적 지표 및 레딧 감정 지표 포함)
            current_portfolio: 현재 포트폴리오
            market_context: 시장 상황
        
        Returns:
            분석된 후보 리스트 (점수 업데이트, 10개로 축소)
        """
        if len(candidates) == 0:
            return candidates
        
        LOGGER.info(f"2차 Ollama 분석 시작: {len(candidates)}개 후보 → 10개 선정")
        
        # Ollama 프롬프트 생성 (토큰 최소화)
        candidates_summary = []
        for c in candidates:
            indicators = c.get("indicators", {})
            candidates_summary.append({
                "market": c.get("market", ""),
                "score": c.get("score", 0.0),
                "trend": c.get("trend", ""),
                "risk": c.get("risk", ""),
                "recent_change": indicators.get("recent_change", 0.0),
                "volume_ratio": indicators.get("volume_ratio", 1.0),
                "volatility": indicators.get("volatility", 0.0),
            })
        
        prompt = f"""다음 10개 암호화폐 후보를 분석하여 매수 우선순위를 재평가하세요.

[후보 코인]
{self._format_candidates_summary(candidates_summary)}

[현재 포트폴리오]
보유 코인: {len(current_portfolio.get('open_positions', []))}개
포트폴리오 분산도: {'높음' if len(current_portfolio.get('open_positions', [])) >= 3 else '낮음'}

[시장 상황]
전체 트렌드: {market_context.get('market_trend', 'unknown')}

분석 기준:
1. 기술적 지표 점수 (기존 점수)
2. 포트폴리오 분산 (중복 방지)
3. 시장 트렌드와의 일치도
4. 리스크 대비 수익 가능성

다음 JSON 형식으로 응답하세요:
{{
  "analysis": [
    {{"market": "KRW-BTC", "adjusted_score": 0.85, "reason": "상승 추세 + 포트폴리오 분산에 유리"}},
    ...
  ]
}}

중요: 기존 점수를 0.1~0.2 범위 내에서만 조정하세요. 간결하게 답변하세요."""

        try:
            # Ollama 2차 분석 모델 사용 (qwen2.5:1.5b)
            response_text = self.second_round_client.generate(
                prompt, 
                temperature=0.3,
                max_retries=2
            )
            data = self.second_round_client.parse_json_response(response_text)
            
            # 분석 결과 반영
            analysis_map = {
                item.get("market"): item 
                for item in data.get("analysis", [])
            }
            
            # 후보 점수 업데이트
            for candidate in candidates:
                market = candidate.get("market", "")
                if market in analysis_map:
                    analysis = analysis_map[market]
                    # 기존 점수와 조정된 점수 평균 (안정성)
                    original_score = candidate.get("score", 0.0)
                    adjusted_score = float(analysis.get("adjusted_score", original_score))
                    candidate["score"] = (original_score * 0.7 + adjusted_score * 0.3)
                    candidate["second_round_reason"] = analysis.get("reason", "")
            
            # 업데이트된 점수 기준 재정렬
            candidates.sort(key=lambda x: x.get("score", 0.0), reverse=True)
            
            LOGGER.info(f"2차 Ollama 분석 완료: {len(candidates)}개 후보 재평가")
            return candidates
            
        except Exception as e:
            LOGGER.warning(f"2차 Ollama 분석 실패: {e}, 원본 후보 반환")
            return candidates

    def analyze_final_decision(
        self,
        final_candidates: list[dict[str, Any]],
        current_portfolio: dict[str, Any],
        market_context: dict[str, Any],
    ) -> tuple[str | None, float, dict[str, Any]]:
        """
        3차 Ollama: 5개 코인에 대해 실제 매매 시그널 분석.
        
        Args:
            final_candidates: 점수 기준으로 선정된 상위 5개 후보
            current_portfolio: 현재 포트폴리오
            market_context: 시장 상황
        
        Returns:
            (selected_market, confidence, decision_data)
        """
        if len(final_candidates) == 0:
            return None, 0.0, {}
        
        LOGGER.info(f"3차 Ollama 매매 시그널 분석 시작: {len(final_candidates)}개 코인")
        
        # 최종 5개 후보 요약 (보유 여부 포함)
        candidates_summary = []
        open_positions = current_portfolio.get("open_positions", [])
        position_map = {pos.get("market"): pos for pos in open_positions}
        
        for c in final_candidates:
            market = c.get("market", "")
            position = position_map.get(market)
            is_held = c.get("is_held", False)
            
            candidate_info = {
                "market": market,
                "score": c.get("score", 0.0),
                "score_eff": c.get("score_eff", c.get("score", 0.0)),
                "trend": c.get("trend", ""),
                "risk": c.get("risk", ""),
                "reason": c.get("reason", ""),
                "is_held": is_held,
            }
            
            # 보유 중인 코인인 경우 포지션 정보 추가
            if is_held and position:
                entry_price = position.get("entry_price", 0)
                current_price = position.get("current_price", entry_price)
                pnl_pct = position.get("pnl_pct", 0)
                position_value = position.get("current_value", 0)
                candidate_info["entry_price"] = entry_price
                candidate_info["current_price"] = current_price
                candidate_info["pnl_pct"] = pnl_pct
                candidate_info["position_value"] = position_value
            
            candidates_summary.append(candidate_info)
        
        prompt = f"""다음 5개 암호화폐는 이미 점수 기준으로 선정된 매매 예정 코인입니다.
각 코인에 대해 매수 타이밍만 판단하세요. (선정은 이미 완료됨)

[매매 예정 5개 코인]
{self._format_candidates_summary(candidates_summary)}

[현재 포트폴리오]
보유 코인: {len(open_positions)}개
최대 포지션: {market_context.get('max_positions', 5)}개

[시장 상황]
전체 트렌드: {market_context.get('market_trend', 'unknown')}

[중요 지침]
- 보유 중인 코인(is_held=true)은 추가 매수 여부를 판단하세요
- 보유 중인 코인이 상승 추세이고 포트폴리오 비중이 적당하면 추가 매수 고려
- 보유 중인 코인이 손실 중이면 평균단가 낮추기 위해 추가 매수 고려 가능
- 보유 중인 코인이 이미 큰 수익 중이면 추가 매수보다는 신규 코인 진입 우선

{self._get_strategy_guidance()}

다음 JSON 형식으로 응답하세요:
{{
  "selected_market": "KRW-BTC",
  "confidence": 0.85,
  "reason": "상승 추세 + 포트폴리오 분산에 유리 + 리스크 낮음",
  "timing": "now",
  "buy_signals": [
    {{"market": "KRW-BTC", "signal": "strong", "timing": "now", "reason": "즉시 매수 타이밍"}},
    {{"market": "KRW-ETH", "signal": "medium", "timing": "watch", "reason": "관찰 중, 하락 시 매수"}},
    {{"market": "KRW-XRP", "signal": "weak", "timing": "wait", "reason": "대기 중, 더 나은 진입점 기다림"}}
  ]
}}

타이밍 옵션:
- "now": 지금 즉시 매수 (강한 시그널, 최적 타이밍)
- "watch": 관찰 중 (조건 충족 시 매수, 가격 변동 모니터링)
- "wait": 대기 중 (더 나은 진입점 기다림)

중요: 
- 5개 코인은 이미 선정되었으므로, 각 코인의 매매 타이밍만 판단하세요
- confidence는 0.0~1.0 범위
- buy_signals에 5개 코인 모두 포함하세요
- 간결하게 답변하세요."""

        try:
            # Ollama 최종 결정 모델 사용 (qwen2.5:1.5b)
            response_text = self.decision_client.generate(
                prompt,
                temperature=0.3,
                max_retries=2
            )
            data = self.decision_client.parse_json_response(response_text)
            
            selected_market = data.get("selected_market")
            confidence = float(data.get("confidence", 0.0))
            reason = data.get("reason", "")
            timing = data.get("timing", "watch")  # AI 타이밍 판단
            buy_signals = data.get("buy_signals", [])
            
            # 각 후보에 매매 시그널 및 타이밍 추가
            signal_map = {item.get("market"): item for item in buy_signals}
            for candidate in final_candidates:
                market = candidate.get("market", "")
                if market in signal_map:
                    signal_info = signal_map[market]
                    candidate["buy_signal"] = signal_info.get("signal", "none")
                    candidate["buy_timing"] = signal_info.get("timing", "watch")
                    candidate["timing_reason"] = signal_info.get("reason", "")
                else:
                    # buy_signals에 없는 경우 기본값 설정
                    candidate["buy_signal"] = "none"
                    candidate["buy_timing"] = "wait"
                    candidate["timing_reason"] = "AI 분석 없음"
            
            LOGGER.info(
                f"3차 Ollama 매매 시그널 분석 완료: {selected_market} "
                f"(신뢰도: {confidence:.2%}, 타이밍: {timing}, 이유: {reason})"
            )
            
            return selected_market, confidence, {
                "reason": reason,
                "timing": timing,  # AI 타이밍 판단 추가
                "buy_signals": buy_signals,
            }
            
        except Exception as e:
            LOGGER.warning(f"3차 Ollama 매매 시그널 분석 실패: {e}, 기본값 설정")
            # 실패 시 모든 코인에 기본 타이밍 설정
            for candidate in final_candidates:
                candidate["buy_signal"] = "none"
                candidate["buy_timing"] = "wait"
                candidate["timing_reason"] = "Ollama 분석 실패, 기본값"
            # 점수 최고 코인 선택
            best = final_candidates[0]
            return best.get("market"), best.get("score_eff", best.get("score", 0.0)), {}

    def _get_strategy_guidance(self) -> str:
        """전략별 매매 가이드라인 반환."""
        if self.high_risk:
            return """[고위험 전략 - 공격적 매매]
- 진입장벽: 낮음 (신뢰도 0.5 이상, 점수 0.5 이상)
- 매수 타이밍: "now" 또는 "watch"에서도 빠른 진입 권장
- "watch" 상태: 가격 변동 0.5% 이상이면 진입 고려 (보수적 조건 완화)
- "wait" 상태: 가격 변동 2% 이상 급락 시 진입 고려
- 변동성: 높은 변동성도 기회로 간주
- 리스크: high risk 코인도 포함 가능
- 손절: 느린 손절 (더 큰 손실 허용)"""
        else:
            return """[저위험 전략 - 보수적 매매]
- 진입장벽: 높음 (신뢰도 0.7 이상, 점수 0.7 이상)
- 매수 타이밍: "now" 타이밍만 진입, "watch"는 엄격한 조건 필요
- "watch" 상태: 가격 변동 1.5% 이상 상승 또는 2% 이상 급락 후 1% 이상 반등 시에만 진입
- "wait" 상태: 진입 금지 (더 나은 기회 대기)
- 변동성: 낮은 변동성 선호
- 리스크: high risk 코인 제외, medium/low만 허용
- 손절: 빠른 손절 (작은 손실로 차단)"""

    def _format_candidates_summary(self, candidates: list[dict[str, Any]]) -> str:
        """후보 요약을 텍스트로 포맷 (보유 여부 포함)."""
        lines = []
        for i, c in enumerate(candidates, 1):
            market = c.get('market', '')
            is_held = c.get('is_held', False)
            held_marker = " [보유 중]" if is_held else " [신규]"
            lines.append(f"{i}. {market}{held_marker}:")
            lines.append(f"   점수: {c.get('score', 0.0):.2f}")
            if 'score_eff' in c:
                lines.append(f"   효과점수: {c.get('score_eff', 0.0):.2f}")
            lines.append(f"   트렌드: {c.get('trend', '')}")
            lines.append(f"   리스크: {c.get('risk', '')}")
            
            # 보유 중인 코인인 경우 포지션 정보 표시
            if is_held:
                if 'entry_price' in c:
                    lines.append(f"   매수가: {c.get('entry_price', 0):,.0f}원")
                if 'current_price' in c:
                    lines.append(f"   현재가: {c.get('current_price', 0):,.0f}원")
                if 'pnl_pct' in c:
                    pnl_pct = c.get('pnl_pct', 0)
                    pnl_marker = "+" if pnl_pct >= 0 else ""
                    lines.append(f"   수익률: {pnl_marker}{pnl_pct:.2f}%")
                if 'position_value' in c:
                    lines.append(f"   포지션 가치: {c.get('position_value', 0):,.0f}원")
            
            if 'recent_change' in c:
                lines.append(f"   최근변화: {c.get('recent_change', 0.0):+.2f}%")
            if 'volume_ratio' in c:
                lines.append(f"   거래량비율: {c.get('volume_ratio', 1.0):.2f}x")
        return "\n".join(lines)

    def analyze_from_remote_scan(
        self,
        scan_results: list[dict[str, Any]],
        current_portfolio: dict[str, Any],
        market_context: dict[str, Any],
    ) -> tuple[StrategySignal, str | None, float, dict[str, Any]]:
        """
        원격 스캔 결과 기반 매매 결정.

        프로세스:
        1차: scan_results 전체 (거래량 상위 30개)
        2차: select_top_candidates로 10개 선정
        3차: 효과 점수 기준으로 상위 5개 최종 선정

        Args:
            scan_results: 서버에서 가져온 스캔 결과 리스트 (1차: 30개)
            current_portfolio: 현재 포트폴리오 정보
            market_context: 전체 시장 상황

        Returns:
            (signal, selected_market, confidence, analysis_data)
        """
        # 현재 포지션 추출
        open_positions = current_portfolio.get("open_positions", [])
        current_positions = [
            pos.get("market", "") for pos in open_positions if pos.get("market")
        ]

        # 1차: 전체 스캔 결과 (거래량 상위 30개)
        first_round_count = len(scan_results)
        LOGGER.info(f"1차 선정: {first_round_count}개 코인 (거래량 상위 30개)")

        # 2차: Ollama로 30개 중 10개로 줄이는 과정에서 AI 사용
        # 먼저 AI 없이 넉넉하게 후보를 선정 (최대 20개 정도)한 후, AI로 10개로 축소
        pre_candidates = self.select_top_candidates(
            scan_results,
            current_positions,
            max_positions=20,  # AI 분석 전 넉넉하게 선정
            portfolio_info=current_portfolio,
            market_context=market_context,
        )

        if not pre_candidates:
            LOGGER.info("매수 후보 없음")
            return StrategySignal.HOLD, None, 0.0, {
                "first_round_count": first_round_count,
                "second_round_count": 0,
                "final_candidates": [],
                "candidates": [],
            }

        LOGGER.info(f"2차 Ollama 분석 전: {len(pre_candidates)}개 코인 (30개 중 선정)")

        # 2차 Ollama: 30개 중 10개로 줄이는 과정에서 AI 사용
        candidates = self.analyze_second_round(
            pre_candidates,
            current_portfolio,
            market_context,
        )
        
        second_round_count = len(candidates)
        LOGGER.info(f"2차 선정 완료: {second_round_count}개 코인 (AI 분석 완료, 30개 → 10개)")

        # 효과 점수(score_eff)를 기준으로 정렬 (동률일 경우 원래 score로 보조 정렬)
        # 매매 예정 5개는 AI 분석 없이 점수 기준으로만 선정
        candidates_sorted = sorted(
            candidates,
            key=lambda c: (
                float(c.get("score_eff", c.get("score", 0.0))),
                float(c.get("score", 0.0)),
            ),
            reverse=True,
        )

        # 매매 예정 5개 선정 (AI 분석 없이 점수 기준으로만)
        top_final = candidates_sorted[:5]
        final_count = len(top_final)
        LOGGER.info(f"매매 예정 5개 선정: {final_count}개 코인 (효과 점수 기준 상위 5개, AI 분석 없음)")

        # 3차 Ollama: 5개 코인에 대해 실제 매매 시그널 분석
        selected_market, confidence, final_decision_data = self.analyze_final_decision(
            top_final,
            current_portfolio,
            market_context,
        )
        
        # Ollama 결정이 없으면 점수 최고 코인 선택 (fallback)
        if not selected_market:
            best = top_final[0]
            selected_market = best.get("market", "")
            confidence = float(best.get("score_eff", best.get("score", 0.0)))

        # 최종 결정 데이터에서 best 코인 찾기
        best = next((c for c in top_final if c.get("market") == selected_market), top_final[0])
        
        # 매수 시그널 생성
        analysis_data = {
            "selected_market": selected_market,
            "confidence": confidence,
            "reason": final_decision_data.get("reason", best.get("reason", "")),
            "trend": best.get("trend", ""),
            "risk": best.get("risk", "medium"),
            # 단계별 선정 결과
            "first_round_count": first_round_count,
            "second_round_count": second_round_count,
            "final_count": final_count,
            # 2차 선정 10개 전체 (분석 콘솔용)
            "second_round_candidates": candidates,
            # 최종 후보 5개에 대한 상세 정보 포함 (base_score / score_eff 포함)
            "final_candidates": top_final,
            # 시장 코드만 묶어둔 단순 리스트도 유지
            "candidates": [c.get("market", "") for c in top_final if c.get("market")],
            # 3차 Ollama 결정 데이터
            "final_decision": final_decision_data,
            "timestamp": datetime.now(UTC).isoformat(),
        }

        LOGGER.info(
            f"매매 결정: {selected_market} BUY (신뢰도 {confidence:.2%}, "
            f"1차: {first_round_count}개 → 2차: {second_round_count}개 → 최종: {final_count}개)"
        )

        return StrategySignal.BUY, selected_market, confidence, analysis_data

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

        # 점수 기준으로 정렬 (기본 순위는 score 기준)
        sorted_analyses = sorted(
            coin_analyses.items(), key=lambda x: x[1].get("score", 0.0), reverse=True
        )
        top_10 = sorted_analyses[:10]  # 상위 10개 선택 (프롬프트 컨텍스트용)

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

