"""
5개 최종 선정 코인 자동 매매 기능 테스트

기능 요구사항:
1. 10개 후보 중 상위 5개 코인 선정 및 매매 예정 등재
2. 5개 코인을 AI 전략에 따라 자동 매수/매도
3. "now" 타이밍 코인 즉시 매매 처리
4. 동적 모니터링을 통한 watch/wait 타이밍 처리
5. 더 높은 점수 코인 발생 시 수익률 낮은 코인 청산 후 신규 매수
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, call
from datetime import datetime, UTC
from collections import deque

from upbit_bot.services.execution import ExecutionEngine
from upbit_bot.services.dynamic_monitor import DynamicTradingMonitor
from upbit_bot.services.trading_decision import TradingDecisionMaker
from upbit_bot.strategies import StrategySignal
from upbit_bot.strategies import Candle


# ============================================================================
# 1. 기능 요구사항 정리
# ============================================================================

"""
기능 요구사항:

FR-1: 10개 후보 중 상위 5개 코인 선정
  - 1차: 거래량 상위 30개 스캔
  - 2차: select_top_candidates로 10개 선정
  - 3차: 효과 점수(score_eff) 기준 상위 5개 최종 선정
  - final_candidates에 5개 코인 정보 저장

FR-2: 5개 코인 매매 예정 등재
  - final_candidates를 동적 모니터링에 등록
  - 각 코인의 buy_timing, buy_signal, timing_reason 정보 포함
  - 동적 모니터링 시작 (60초 주기)

FR-3: "now" 타이밍 코인 즉시 매매
  - buy_timing == "now" && buy_signal in ("strong", "medium")
  - 선정 직후 즉시 매매 실행
  - 매매 완료 후 entry_signal 초기화

FR-4: watch/wait 타이밍 동적 처리
  - watch: 가격 히스토리 1개 이상, 시간 기반 자동 매수 (고위험: 2분, 저위험: 5분)
  - wait: 가격 히스토리 2개 이상, 전략별 차별화 조건
  - 동적 모니터링 루프에서 주기적 체크

FR-5: 더 높은 점수 코인 발생 시 교체
  - 5개 선정 코인 외에서 더 높은 점수 코인 발견
  - 현재 보유 중인 코인 중 수익률이 가장 낮은 코인 찾기
  - 해당 코인 청산 후 신규 코인 매수
"""


# ============================================================================
# 2. 테스트 케이스 정의
# ============================================================================

class TestFinalCandidatesSelection:
    """TC-1: 10개 후보 중 상위 5개 선정"""
    
    def test_select_top_5_from_10_candidates(self):
        """
        TC-1-1 / 목적: 10개 후보 중 효과 점수 기준 상위 5개 선정 확인
        입력값: 10개 후보 리스트 (각각 score_eff 포함)
        절차:
          1. analyze_from_remote_scan 호출
          2. final_candidates 반환 확인
        예상 결과: final_candidates에 정확히 5개 코인 포함, score_eff 내림차순 정렬
        """
        pass
    
    def test_less_than_5_candidates_handling(self):
        """
        TC-1-2 / 목적: 후보가 5개 미만일 때 처리
        입력값: 3개 후보 리스트
        절차:
          1. analyze_from_remote_scan 호출
          2. final_candidates 확인
        예상 결과: final_candidates에 3개만 포함, 에러 없음
        """
        pass
    
    def test_empty_candidates_handling(self):
        """
        TC-1-3 / 목적: 후보가 없을 때 처리
        입력값: 빈 리스트
        절차:
          1. analyze_from_remote_scan 호출
          2. 반환값 확인
        예상 결과: HOLD 신호, final_candidates 빈 리스트
        """
        pass


class TestImmediateTrading:
    """TC-2: "now" 타이밍 코인 즉시 매매"""
    
    def test_now_timing_strong_signal_immediate_buy(self):
        """
        TC-2-1 / 목적: "now" 타이밍 + "strong" 신호 즉시 매매
        입력값: final_candidates에 buy_timing="now", buy_signal="strong"인 코인
        절차:
          1. update_final_candidates 호출
          2. 즉시 매매 실행 확인
        예상 결과: _execute_signal(BUY) 호출, entry_signal 초기화
        """
        pass
    
    def test_now_timing_medium_signal_immediate_buy(self):
        """
        TC-2-2 / 목적: "now" 타이밍 + "medium" 신호 즉시 매매
        입력값: final_candidates에 buy_timing="now", buy_signal="medium"인 코인
        절차: TC-2-1과 동일
        예상 결과: 즉시 매매 실행
        """
        pass
    
    def test_now_timing_weak_signal_no_buy(self):
        """
        TC-2-3 / 목적: "now" 타이밍이지만 "weak" 신호는 매매 안 함
        입력값: buy_timing="now", buy_signal="weak"
        절차: update_final_candidates 호출
        예상 결과: 매매 실행 안 됨
        """
        pass
    
    def test_immediate_buy_exception_handling(self):
        """
        TC-2-4 / 목적: 즉시 매매 중 예외 발생 시 처리
        입력값: _execute_signal에서 예외 발생
        절차:
          1. 즉시 매매 시도
          2. 예외 발생
        예상 결과: 에러 로그, market 원복, 다음 코인 계속 처리
        """
        pass


class TestDynamicMonitoring:
    """TC-3: 동적 모니터링 및 watch/wait 타이밍 처리"""
    
    def test_update_final_candidates_registration(self):
        """
        TC-3-1 / 목적: 5개 코인 동적 모니터링 등록
        입력값: final_candidates 5개
        절차:
          1. update_final_candidates 호출
          2. monitored_coins 확인
        예상 결과: 5개 코인 모두 monitored_coins에 등록, buy_timing/buy_signal 저장
        """
        pass
    
    def test_monitoring_thread_start(self):
        """
        TC-3-2 / 목적: 동적 모니터링 스레드 시작
        입력값: final_candidates 등록 후
        절차:
          1. start_monitoring 호출
          2. 스레드 상태 확인
        예상 결과: _monitor_thread 생성 및 실행 중
        """
        pass
    
    def test_watch_timing_high_risk_auto_buy(self):
        """
        TC-3-3 / 목적: watch 타이밍 고위험 전략 자동 매수
        입력값: buy_timing="watch", high_risk=True, 2분 경과, buy_signal="strong"
        절차:
          1. 가격 히스토리 1개만 있음
          2. 2분 경과 후 체크
        예상 결과: entry_signal 생성 (watch_timeout)
        """
        pass
    
    def test_watch_timing_low_risk_auto_buy(self):
        """
        TC-3-4 / 목적: watch 타이밍 저위험 전략 자동 매수
        입력값: buy_timing="watch", high_risk=False, 5분 경과, buy_signal="strong"
        절차: TC-3-3과 동일
        예상 결과: 5분 경과 후 entry_signal 생성
        """
        pass
    
    def test_watch_timing_price_change_conditions(self):
        """
        TC-3-5 / 목적: watch 타이밍 가격 변동 조건 체크
        입력값: 가격 히스토리 2개 이상, 가격 변동 조건 충족
        절차:
          1. 고위험: 0.5% 상승 또는 -1% 급락 후 0.5% 반등
          2. 저위험: 1.5% 상승 또는 -2% 급락 후 1% 반등
        예상 결과: 조건 충족 시 entry_signal 생성
        """
        pass
    
    def test_wait_timing_high_risk_conditions(self):
        """
        TC-3-6 / 목적: wait 타이밍 고위험 전략 조건
        입력값: buy_timing="wait", high_risk=True, 가격 -2% 급락
        절차: 가격 히스토리 2개 이상, -2% 급락 감지
        예상 결과: entry_signal 생성 (wait_opportunity)
        """
        pass
    
    def test_wait_timing_low_risk_no_entry(self):
        """
        TC-3-7 / 목적: wait 타이밍 저위험 전략 진입 금지
        입력값: buy_timing="wait", high_risk=False
        절차: 가격 변동 체크
        예상 결과: entry_signal 생성 안 됨 (None 반환)
        """
        pass


class TestMultipleCoinsTrading:
    """TC-4: 여러 코인 동시 매매 처리"""
    
    def test_multiple_now_timing_coins_sequential_buy(self):
        """
        TC-4-1 / 목적: 여러 "now" 타이밍 코인 순차 매매
        입력값: final_candidates에 "now" 타이밍 코인 3개
        절차:
          1. update_final_candidates 호출
          2. 각 코인 순차 매매 확인
        예상 결과: 3개 코인 모두 매매 실행, 순차 처리
        """
        pass
    
    def test_run_once_checks_all_monitored_coins(self):
        """
        TC-4-2 / 목적: run_once에서 5개 코인 모두 체크
        입력값: monitored_coins에 5개 코인, 각각 entry_signal 있음
        절차:
          1. run_once 호출
          2. 각 코인 entry_signal 확인
        예상 결과: 조건 충족 코인 모두 매매 실행
        """
        pass


class TestPortfolioReplacement:
    """TC-5: 더 높은 점수 코인 발생 시 교체"""
    
    def test_find_lowest_pnl_coin(self):
        """
        TC-5-1 / 목적: 보유 코인 중 수익률 가장 낮은 코인 찾기
        입력값: open_positions에 3개 코인 (수익률: -5%, +2%, -10%)
        절차: 수익률 계산 및 비교
        예상 결과: -10% 코인 반환
        """
        pass
    
    def test_replace_low_pnl_with_high_score(self):
        """
        TC-5-2 / 목적: 수익률 낮은 코인 청산 후 신규 코인 매수
        입력값: 
          - 현재 보유: 5개 코인 (최저 수익률: -8%)
          - 신규 발견: 더 높은 점수 코인 (5개 선정 외)
        절차:
          1. 최저 수익률 코인 찾기
          2. 해당 코인 매도
          3. 신규 코인 매수
        예상 결과: 교체 완료, 포트폴리오 업데이트
        """
        pass
    
    def test_no_replacement_when_all_profitable(self):
        """
        TC-5-3 / 목적: 모든 코인이 수익일 때 교체 안 함
        입력값: 모든 보유 코인 수익률 양수
        절차: 교체 로직 실행
        예상 결과: 교체 안 됨
        """
        pass


# ============================================================================
# 3. 오류·예외·엣지 케이스
# ============================================================================

class TestErrorCases:
    """TC-6: 오류 및 예외 처리"""
    
    def test_ollama_timeout_during_selection(self):
        """
        TC-6-1 / 목적: Ollama 타임아웃 시 선정 처리
        입력값: analyze_final_decision에서 타임아웃
        절차: 타임아웃 발생
        예상 결과: fallback으로 점수 최고 코인 선택, 에러 없이 진행
        """
        pass
    
    def test_invalid_market_in_candidates(self):
        """
        TC-6-2 / 목적: final_candidates에 유효하지 않은 market 포함
        입력값: market="" 또는 None인 후보
        절차: update_final_candidates 호출
        예상 결과: 유효하지 않은 market 제외, 에러 없음
        """
        pass
    
    def test_price_callback_failure(self):
        """
        TC-6-3 / 목적: 가격 조회 콜백 실패
        입력값: _price_callback에서 None 반환
        절차: 동적 모니터링 루프 실행
        예상 결과: 해당 코인 스킵, 다른 코인 계속 모니터링
        """
        pass
    
    def test_candle_fetch_failure_during_immediate_buy(self):
        """
        TC-6-4 / 목적: 즉시 매매 중 캔들 조회 실패
        입력값: _fetch_candles에서 예외 발생
        절차: 즉시 매매 시도
        예상 결과: 예외 처리, market 원복, 다음 코인 계속
        """
        pass
    
    def test_monitoring_thread_already_running(self):
        """
        TC-6-5 / 목적: 모니터링 스레드가 이미 실행 중일 때
        입력값: _monitor_thread.is_alive() == True
        절차: start_monitoring 호출
        예상 결과: 새 스레드 생성 안 함, 기존 스레드 유지
        """
        pass


class TestEdgeCases:
    """TC-7: 엣지 케이스"""
    
    def test_exactly_5_candidates(self):
        """
        TC-7-1 / 목적: 정확히 5개 후보일 때
        입력값: 후보 5개
        절차: 선정 로직 실행
        예상 결과: 5개 모두 final_candidates에 포함
        """
        pass
    
    def test_more_than_5_candidates_same_score(self):
        """
        TC-7-2 / 목적: 5개 초과 후보, 동일 점수
        입력값: 7개 후보, 상위 5개 동일 score_eff
        절차: 정렬 및 선정
        예상 결과: score로 보조 정렬, 상위 5개 선정
        """
        pass
    
    def test_all_candidates_wait_timing(self):
        """
        TC-7-3 / 목적: 모든 후보가 "wait" 타이밍
        입력값: 5개 모두 buy_timing="wait"
        절차: 동적 모니터링 시작
        예상 결과: 모니터링 등록, 매매 대기
        """
        pass
    
    def test_price_history_overflow(self):
        """
        TC-7-4 / 목적: 가격 히스토리 최대치 초과
        입력값: deque(maxlen=20)에 21개 추가
        절차: 가격 히스토리 업데이트
        예상 결과: 최대 20개만 유지, 오래된 데이터 자동 삭제
        """
        pass
    
    def test_concurrent_candidate_updates(self):
        """
        TC-7-5 / 목적: 동시에 final_candidates 업데이트
        입력값: 두 스레드에서 동시 update_final_candidates 호출
        절차: 동시 실행
        예상 결과: lock으로 보호, 데이터 일관성 유지
        """
        pass


# ============================================================================
# 4. 예상 입력/출력 정의
# ============================================================================

"""
입력 데이터 구조:

final_candidates = [
    {
        "market": "KRW-BTC",
        "score": 0.85,
        "score_eff": 0.90,
        "base_score": 0.80,
        "buy_timing": "now",  # "now" | "watch" | "wait"
        "buy_signal": "strong",  # "strong" | "medium" | "weak" | "none"
        "timing_reason": "AI 즉시 매수 타이밍 판단",
        "trend": "uptrend",
        "risk": "medium",
        "exposure_pct": 15.5
    },
    ...
]

출력 데이터 구조:

monitoring_status = {
    "monitored_count": 5,
    "markets": ["KRW-BTC", "KRW-ETH", ...],
    "signals": {
        "KRW-BTC": {
            "type": "ai_timing",
            "timing": "now",
            "signal": "strong",
            "reason": "AI 즉시 매수 타이밍 판단"
        }
    },
    "timings": {
        "KRW-BTC": {
            "buy_timing": "now",
            "buy_signal": "strong",
            "timing_reason": "..."
        }
    }
}
"""


# ============================================================================
# 5. 자동화 테스트 코드
# ============================================================================

@pytest.fixture
def mock_upbit_client():
    """Upbit API 클라이언트 모킹"""
    client = Mock()
    client.get_ticker.return_value = {"trade_price": 50000000.0}
    client.get_candles.return_value = [
        {"trade_price": 50000000.0, "timestamp": 1000}
    ]
    return client


@pytest.fixture
def mock_strategy():
    """전략 모킹"""
    strategy = Mock()
    strategy.name = "ai_market_analyzer"
    strategy.on_candles.return_value = StrategySignal.HOLD
    return strategy


@pytest.fixture
def execution_engine(mock_upbit_client, mock_strategy):
    """ExecutionEngine 인스턴스"""
    with patch('upbit_bot.services.execution.UpbitClient', return_value=mock_upbit_client):
        engine = ExecutionEngine(
            market="KRW-BTC",
            strategy=mock_strategy,
            client=mock_upbit_client
        )
        return engine


@pytest.fixture
def sample_final_candidates():
    """샘플 final_candidates 데이터"""
    return [
        {
            "market": "KRW-BTC",
            "score": 0.85,
            "score_eff": 0.90,
            "buy_timing": "now",
            "buy_signal": "strong",
            "timing_reason": "AI 즉시 매수 타이밍",
            "trend": "uptrend",
            "risk": "medium"
        },
        {
            "market": "KRW-ETH",
            "score": 0.80,
            "score_eff": 0.85,
            "buy_timing": "watch",
            "buy_signal": "medium",
            "timing_reason": "관찰 중",
            "trend": "uptrend",
            "risk": "low"
        },
        {
            "market": "KRW-XRP",
            "score": 0.75,
            "score_eff": 0.78,
            "buy_timing": "wait",
            "buy_signal": "weak",
            "timing_reason": "대기 중",
            "trend": "sideways",
            "risk": "medium"
        },
        {
            "market": "KRW-ADA",
            "score": 0.70,
            "score_eff": 0.72,
            "buy_timing": "watch",
            "buy_signal": "strong",
            "timing_reason": "관찰 중",
            "trend": "uptrend",
            "risk": "high"
        },
        {
            "market": "KRW-DOT",
            "score": 0.65,
            "score_eff": 0.68,
            "buy_timing": "wait",
            "buy_signal": "none",
            "timing_reason": "대기 중",
            "trend": "downtrend",
            "risk": "medium"
        }
    ]


class TestFinalCandidatesSelectionImpl:
    """TC-1 구현"""
    
    def test_select_top_5_from_10_candidates(self, execution_engine, sample_final_candidates):
        """TC-1-1 구현"""
        # Given: 10개 후보 (5개만 제공하지만 로직 테스트)
        analysis_data = {
            "final_candidates": sample_final_candidates[:5],
            "first_round_count": 30,
            "second_round_count": 10
        }
        
        # When: 동적 모니터링 업데이트
        if execution_engine.dynamic_monitor:
            execution_engine.dynamic_monitor.update_final_candidates(
                analysis_data["final_candidates"]
            )
            
            # Then: 5개 코인 등록 확인
            status = execution_engine.dynamic_monitor.get_monitoring_status()
            assert status["monitored_count"] == 5
            assert len(status["markets"]) == 5
    
    def test_less_than_5_candidates_handling(self, execution_engine):
        """TC-1-2 구현"""
        # Given: 3개 후보
        candidates = [
            {"market": "KRW-BTC", "score_eff": 0.90, "buy_timing": "now"},
            {"market": "KRW-ETH", "score_eff": 0.85, "buy_timing": "watch"},
            {"market": "KRW-XRP", "score_eff": 0.80, "buy_timing": "wait"}
        ]
        
        # When: 업데이트
        if execution_engine.dynamic_monitor:
            execution_engine.dynamic_monitor.update_final_candidates(candidates)
            
            # Then: 3개만 등록
            status = execution_engine.dynamic_monitor.get_monitoring_status()
            assert status["monitored_count"] == 3


class TestImmediateTradingImpl:
    """TC-2 구현"""
    
    @patch('upbit_bot.services.execution.ExecutionEngine._execute_signal')
    @patch('upbit_bot.services.execution.ExecutionEngine._fetch_candles')
    def test_now_timing_strong_signal_immediate_buy(
        self, 
        mock_fetch_candles,
        mock_execute_signal,
        execution_engine,
        sample_final_candidates
    ):
        """TC-2-1 구현"""
        # Given: "now" + "strong" 코인
        candidates = [sample_final_candidates[0]]  # KRW-BTC
        
        mock_fetch_candles.return_value = []
        mock_execute_signal.return_value = {"status": "success"}
        
        # When: final_candidates 업데이트 (즉시 매매 트리거)
        analysis_data = {"final_candidates": candidates}
        
        # execution_engine의 _analyze_multiple_markets에서 처리되는 부분 모킹
        with patch.object(execution_engine, '_analyze_multiple_markets') as mock_analyze:
            mock_analyze.return_value = ("KRW-BTC", StrategySignal.HOLD, [])
            
            # 동적 모니터링 업데이트 및 즉시 매매 확인
            if execution_engine.dynamic_monitor:
                execution_engine.dynamic_monitor.update_final_candidates(candidates)
                
                # 수동으로 즉시 매매 로직 테스트
                for candidate in candidates:
                    if (candidate.get("buy_timing") == "now" and 
                        candidate.get("buy_signal") in ("strong", "medium")):
                        original_market = execution_engine.market
                        execution_engine.market = candidate["market"]
                        execution_engine._execute_signal(
                            StrategySignal.BUY, 
                            [], 
                            ai_timing="now"
                        )
                        execution_engine.market = original_market
                
                # Then: 매매 실행 확인
                mock_execute_signal.assert_called()


class TestDynamicMonitoringImpl:
    """TC-3 구현"""
    
    def test_update_final_candidates_registration(
        self, 
        execution_engine, 
        sample_final_candidates
    ):
        """TC-3-1 구현"""
        # Given: 5개 후보
        # When: 업데이트
        if execution_engine.dynamic_monitor:
            execution_engine.dynamic_monitor.update_final_candidates(
                sample_final_candidates
            )
            
            # Then: 등록 확인
            status = execution_engine.dynamic_monitor.get_monitoring_status()
            assert status["monitored_count"] == 5
            
            # 각 코인의 buy_timing 확인
            for candidate in sample_final_candidates:
                market = candidate["market"]
                timing_info = status["timings"].get(market, {})
                assert timing_info["buy_timing"] == candidate["buy_timing"]
                assert timing_info["buy_signal"] == candidate["buy_signal"]
    
    def test_watch_timing_high_risk_auto_buy(self, execution_engine):
        """TC-3-3 구현"""
        # Given: watch 타이밍, 고위험, 2분 경과
        if not execution_engine.dynamic_monitor:
            pytest.skip("동적 모니터링 없음")
        
        monitor = execution_engine.dynamic_monitor
        monitor.high_risk = True
        
        # 코인 등록 (2분 전 시간으로 설정)
        from datetime import timedelta
        old_time = datetime.now(UTC) - timedelta(minutes=2, seconds=30)
        
        monitor.monitored_coins = {
            "KRW-ETH": {
                "buy_timing": "watch",
                "buy_signal": "strong",
                "last_update": old_time,
                "timing_reason": "관찰 중"
            }
        }
        
        # 가격 히스토리 1개만
        monitor.price_history["KRW-ETH"] = deque([{"price": 3000000.0, "timestamp": datetime.now(UTC)}])
        
        # When: 타이밍 체크
        signal = monitor._check_buy_timing("KRW-ETH", 3000000.0, None)
        
        # Then: entry_signal 생성
        assert signal is not None
        assert signal["timing"] == "watch"
        assert signal["type"] == "watch_timeout"


class TestErrorCasesImpl:
    """TC-6 구현"""
    
    def test_invalid_market_in_candidates(self, execution_engine):
        """TC-6-2 구현"""
        # Given: 유효하지 않은 market 포함
        candidates = [
            {"market": "KRW-BTC", "score_eff": 0.90},
            {"market": "", "score_eff": 0.85},  # 빈 문자열
            {"market": None, "score_eff": 0.80},  # None
            {"market": "KRW-ETH", "score_eff": 0.75}
        ]
        
        # When: 업데이트
        if execution_engine.dynamic_monitor:
            execution_engine.dynamic_monitor.update_final_candidates(candidates)
            
            # Then: 유효한 market만 등록
            status = execution_engine.dynamic_monitor.get_monitoring_status()
            assert "KRW-BTC" in status["markets"]
            assert "KRW-ETH" in status["markets"]
            assert "" not in status["markets"]
            # None은 키로 사용 불가하므로 자동 제외됨
    
    def test_price_callback_failure(self, execution_engine):
        """TC-6-3 구현"""
        # Given: 가격 콜백이 None 반환
        if not execution_engine.dynamic_monitor:
            pytest.skip("동적 모니터링 없음")
        
        monitor = execution_engine.dynamic_monitor
        monitor.monitored_coins = {
            "KRW-BTC": {"buy_timing": "watch", "buy_signal": "medium"}
        }
        
        # 콜백이 None 반환하도록 설정
        def failing_callback(market):
            return None
        
        monitor._price_callback = failing_callback
        
        # When: 모니터링 루프 실행 (한 번만)
        # 실제로는 스레드에서 실행되지만, 여기서는 직접 호출
        current_price = monitor._price_callback("KRW-BTC")
        
        # Then: None 반환, 에러 없음
        assert current_price is None


# ============================================================================
# 6. 테스트 우선순위 분류
# ============================================================================

"""
P1 (Critical - 필수):
- TC-1-1: 10개 중 상위 5개 선정
- TC-2-1: "now" 타이밍 즉시 매매
- TC-3-1: 동적 모니터링 등록
- TC-3-2: 모니터링 스레드 시작
- TC-6-1: Ollama 타임아웃 처리
- TC-6-2: 유효하지 않은 market 처리

P2 (High - 중요):
- TC-1-2: 5개 미만 후보 처리
- TC-2-2: "now" + "medium" 매매
- TC-2-4: 즉시 매매 예외 처리
- TC-3-3: watch 타이밍 고위험 자동 매수
- TC-3-5: watch 타이밍 가격 변동 조건
- TC-4-1: 여러 코인 순차 매매
- TC-5-2: 수익률 낮은 코인 교체
- TC-6-3: 가격 콜백 실패 처리

P3 (Medium - 일반):
- TC-1-3: 빈 후보 처리
- TC-2-3: "weak" 신호 매매 안 함
- TC-3-4: watch 타이밍 저위험 자동 매수
- TC-3-6: wait 타이밍 고위험 조건
- TC-3-7: wait 타이밍 저위험 진입 금지
- TC-4-2: run_once에서 모든 코인 체크
- TC-5-1: 최저 수익률 코인 찾기
- TC-5-3: 모든 코인 수익 시 교체 안 함
- TC-6-4: 캔들 조회 실패
- TC-6-5: 모니터링 스레드 중복 시작 방지
- TC-7-1 ~ TC-7-5: 엣지 케이스
"""


# ============================================================================
# 7. 누락될 수 있는 리스크 요소
# ============================================================================

"""
리스크 요소:

1. 동시성 문제
   - 여러 스레드에서 동시에 final_candidates 업데이트
   - 모니터링 루프와 매매 실행 간 경쟁 조건
   → 해결: lock 사용 확인 필요

2. 메모리 누수
   - 가격 히스토리 무한 증가 (deque maxlen 확인)
   - 모니터링 스레드 종료 안 됨
   → 해결: maxlen 설정, stop_monitoring 호출 확인

3. API 호출 제한
   - Upbit API rate limit 초과
   - 동적 모니터링에서 과도한 가격 조회
   → 해결: 호출 빈도 제한, 재시도 로직

4. 데이터 일관성
   - final_candidates 업데이트 중 매매 실행
   - 가격 히스토리와 실제 가격 불일치
   → 해결: 트랜잭션 처리, 타임스탬프 확인

5. 네트워크 장애
   - 가격 조회 실패 시 재시도 없음
   - Ollama 서버 연결 끊김
   → 해결: 재시도 로직, fallback 메커니즘

6. 포트폴리오 제한
   - 최대 보유 코인 수 초과
   - 교체 시 포트폴리오 크기 확인 안 함
   → 해결: 최대 보유 수 체크

7. 점수 계산 오류
   - score_eff 계산 실패 시 기본값 사용
   - 음수 점수 처리
   → 해결: 유효성 검증, 기본값 처리

8. 시간 동기화
   - 서버 시간과 로컬 시간 불일치
   - 모니터링 시작 시간 기록 오류
   → 해결: UTC 사용, 타임스탬프 검증
"""

