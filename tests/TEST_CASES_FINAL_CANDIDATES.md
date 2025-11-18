# 5개 최종 선정 코인 자동 매매 기능 테스트 케이스

## 1. 기능 요구사항 정리

### FR-1: 10개 후보 중 상위 5개 코인 선정
- **1차**: 거래량 상위 30개 스캔
- **2차**: `select_top_candidates`로 10개 선정 (점수 및 거래량 기준)
- **3차**: 효과 점수(`score_eff`) 기준 상위 5개 최종 선정
- `final_candidates`에 5개 코인 정보 저장

### FR-2: 5개 코인 매매 예정 등재
- `final_candidates`를 동적 모니터링(`DynamicTradingMonitor`)에 등록
- 각 코인의 `buy_timing`, `buy_signal`, `timing_reason` 정보 포함
- 동적 모니터링 시작 (60초 주기)

### FR-3: "now" 타이밍 코인 즉시 매매
- 조건: `buy_timing == "now"` && `buy_signal in ("strong", "medium")`
- 선정 직후 즉시 매매 실행
- 매매 완료 후 `entry_signal` 초기화

### FR-4: watch/wait 타이밍 동적 처리
- **watch**: 가격 히스토리 1개 이상, 시간 기반 자동 매수
  - 고위험: 2분 경과 + strong/medium 신호
  - 저위험: 5분 경과 + strong 신호
- **wait**: 가격 히스토리 2개 이상, 전략별 차별화 조건
  - 고위험: -2% 급락 시 매수 기회
  - 저위험: 진입 금지
- 동적 모니터링 루프에서 주기적 체크

### FR-5: 더 높은 점수 코인 발생 시 교체
- 5개 선정 코인 외에서 더 높은 점수 코인 발견
- 현재 보유 중인 코인 중 수익률이 가장 낮은 코인 찾기
- 해당 코인 청산 후 신규 코인 매수

---

## 2. 정상 시나리오 테스트 케이스

### TC-1: 10개 후보 중 상위 5개 선정

| TC ID | 목적 | 입력값 | 절차 | 예상 결과 |
|-------|------|--------|------|-----------|
| TC-1-1 | 10개 후보 중 효과 점수 기준 상위 5개 선정 확인 | 10개 후보 리스트 (각각 `score_eff` 포함) | 1. `analyze_from_remote_scan` 호출<br>2. `final_candidates` 반환 확인 | `final_candidates`에 정확히 5개 코인 포함, `score_eff` 내림차순 정렬 |
| TC-1-2 | 후보가 5개 미만일 때 처리 | 3개 후보 리스트 | 1. `analyze_from_remote_scan` 호출<br>2. `final_candidates` 확인 | `final_candidates`에 3개만 포함, 에러 없음 |
| TC-1-3 | 후보가 없을 때 처리 | 빈 리스트 | 1. `analyze_from_remote_scan` 호출<br>2. 반환값 확인 | HOLD 신호, `final_candidates` 빈 리스트 |

### TC-2: "now" 타이밍 코인 즉시 매매

| TC ID | 목적 | 입력값 | 절차 | 예상 결과 |
|-------|------|--------|------|-----------|
| TC-2-1 | "now" 타이밍 + "strong" 신호 즉시 매매 | `final_candidates`에 `buy_timing="now"`, `buy_signal="strong"`인 코인 | 1. `update_final_candidates` 호출<br>2. 즉시 매매 실행 확인 | `_execute_signal(BUY)` 호출, `entry_signal` 초기화 |
| TC-2-2 | "now" 타이밍 + "medium" 신호 즉시 매매 | `buy_timing="now"`, `buy_signal="medium"` | TC-2-1과 동일 | 즉시 매매 실행 |
| TC-2-3 | "now" 타이밍이지만 "weak" 신호는 매매 안 함 | `buy_timing="now"`, `buy_signal="weak"` | `update_final_candidates` 호출 | 매매 실행 안 됨 |

### TC-3: 동적 모니터링 및 watch/wait 타이밍 처리

| TC ID | 목적 | 입력값 | 절차 | 예상 결과 |
|-------|------|--------|------|-----------|
| TC-3-1 | 5개 코인 동적 모니터링 등록 | `final_candidates` 5개 | 1. `update_final_candidates` 호출<br>2. `monitored_coins` 확인 | 5개 코인 모두 `monitored_coins`에 등록, `buy_timing`/`buy_signal` 저장 |
| TC-3-2 | 동적 모니터링 스레드 시작 | `final_candidates` 등록 후 | 1. `start_monitoring` 호출<br>2. 스레드 상태 확인 | `_monitor_thread` 생성 및 실행 중 |
| TC-3-3 | watch 타이밍 고위험 전략 자동 매수 | `buy_timing="watch"`, `high_risk=True`, 2분 경과, `buy_signal="strong"` | 1. 가격 히스토리 1개만 있음<br>2. 2분 경과 후 체크 | `entry_signal` 생성 (`watch_timeout`) |
| TC-3-4 | watch 타이밍 저위험 전략 자동 매수 | `buy_timing="watch"`, `high_risk=False`, 5분 경과, `buy_signal="strong"` | TC-3-3과 동일 | 5분 경과 후 `entry_signal` 생성 |
| TC-3-5 | watch 타이밍 가격 변동 조건 체크 | 가격 히스토리 2개 이상, 가격 변동 조건 충족 | 1. 고위험: 0.5% 상승 또는 -1% 급락 후 0.5% 반등<br>2. 저위험: 1.5% 상승 또는 -2% 급락 후 1% 반등 | 조건 충족 시 `entry_signal` 생성 |
| TC-3-6 | wait 타이밍 고위험 전략 조건 | `buy_timing="wait"`, `high_risk=True`, 가격 -2% 급락 | 가격 히스토리 2개 이상, -2% 급락 감지 | `entry_signal` 생성 (`wait_opportunity`) |
| TC-3-7 | wait 타이밍 저위험 전략 진입 금지 | `buy_timing="wait"`, `high_risk=False` | 가격 변동 체크 | `entry_signal` 생성 안 됨 (None 반환) |

### TC-4: 여러 코인 동시 매매 처리

| TC ID | 목적 | 입력값 | 절차 | 예상 결과 |
|-------|------|--------|------|-----------|
| TC-4-1 | 여러 "now" 타이밍 코인 순차 매매 | `final_candidates`에 "now" 타이밍 코인 3개 | 1. `update_final_candidates` 호출<br>2. 각 코인 순차 매매 확인 | 3개 코인 모두 매매 실행, 순차 처리 |
| TC-4-2 | `run_once`에서 5개 코인 모두 체크 | `monitored_coins`에 5개 코인, 각각 `entry_signal` 있음 | 1. `run_once` 호출<br>2. 각 코인 `entry_signal` 확인 | 조건 충족 코인 모두 매매 실행 |

### TC-5: 더 높은 점수 코인 발생 시 교체

| TC ID | 목적 | 입력값 | 절차 | 예상 결과 |
|-------|------|--------|------|-----------|
| TC-5-1 | 보유 코인 중 수익률 가장 낮은 코인 찾기 | `open_positions`에 3개 코인 (수익률: -5%, +2%, -10%) | 수익률 계산 및 비교 | -10% 코인 반환 |
| TC-5-2 | 수익률 낮은 코인 청산 후 신규 코인 매수 | 현재 보유: 5개 코인 (최저 수익률: -8%)<br>신규 발견: 더 높은 점수 코인 | 1. 최저 수익률 코인 찾기<br>2. 해당 코인 매도<br>3. 신규 코인 매수 | 교체 완료, 포트폴리오 업데이트 |
| TC-5-3 | 모든 코인이 수익일 때 교체 안 함 | 모든 보유 코인 수익률 양수 | 교체 로직 실행 | 교체 안 됨 |

---

## 3. 오류·예외·엣지 케이스 테스트

### TC-6: 오류 및 예외 처리

| TC ID | 목적 | 입력값 | 절차 | 예상 결과 |
|-------|------|--------|------|-----------|
| TC-6-1 | Ollama 타임아웃 시 선정 처리 | `analyze_final_decision`에서 타임아웃 | 타임아웃 발생 | fallback으로 점수 최고 코인 선택, 에러 없이 진행 |
| TC-6-2 | `final_candidates`에 유효하지 않은 market 포함 | `market=""` 또는 `None`인 후보 | `update_final_candidates` 호출 | 유효하지 않은 market 제외, 에러 없음 |
| TC-6-3 | 가격 조회 콜백 실패 | `_price_callback`에서 `None` 반환 | 동적 모니터링 루프 실행 | 해당 코인 스킵, 다른 코인 계속 모니터링 |
| TC-6-4 | 즉시 매매 중 캔들 조회 실패 | `_fetch_candles`에서 예외 발생 | 즉시 매매 시도 | 예외 처리, market 원복, 다음 코인 계속 |
| TC-6-5 | 모니터링 스레드가 이미 실행 중일 때 | `_monitor_thread.is_alive() == True` | `start_monitoring` 호출 | 새 스레드 생성 안 함, 기존 스레드 유지 |

### TC-7: 엣지 케이스

| TC ID | 목적 | 입력값 | 절차 | 예상 결과 |
|-------|------|--------|------|-----------|
| TC-7-1 | 정확히 5개 후보일 때 | 후보 5개 | 선정 로직 실행 | 5개 모두 `final_candidates`에 포함 |
| TC-7-2 | 5개 초과 후보, 동일 점수 | 7개 후보, 상위 5개 동일 `score_eff` | 정렬 및 선정 | `score`로 보조 정렬, 상위 5개 선정 |
| TC-7-3 | 모든 후보가 "wait" 타이밍 | 5개 모두 `buy_timing="wait"` | 동적 모니터링 시작 | 모니터링 등록, 매매 대기 |
| TC-7-4 | 가격 히스토리 최대치 초과 | `deque(maxlen=20)`에 21개 추가 | 가격 히스토리 업데이트 | 최대 20개만 유지, 오래된 데이터 자동 삭제 |
| TC-7-5 | 동시에 `final_candidates` 업데이트 | 두 스레드에서 동시 `update_final_candidates` 호출 | 동시 실행 | lock으로 보호, 데이터 일관성 유지 |

---

## 4. 예상 입력/출력 정의

### 입력 데이터 구조

```python
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
    # ... 4개 더
]
```

### 출력 데이터 구조

```python
monitoring_status = {
    "monitored_count": 5,
    "markets": ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-ADA", "KRW-DOT"],
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
            "timing_reason": "AI 즉시 매수 타이밍 판단"
        }
    }
}
```

---

## 5. 테스트 우선순위 분류

### P1 (Critical - 필수)
- TC-1-1: 10개 중 상위 5개 선정
- TC-2-1: "now" 타이밍 즉시 매매
- TC-3-1: 동적 모니터링 등록
- TC-3-2: 모니터링 스레드 시작
- TC-6-1: Ollama 타임아웃 처리
- TC-6-2: 유효하지 않은 market 처리

### P2 (High - 중요)
- TC-1-2: 5개 미만 후보 처리
- TC-2-2: "now" + "medium" 매매
- TC-2-4: 즉시 매매 예외 처리
- TC-3-3: watch 타이밍 고위험 자동 매수
- TC-3-5: watch 타이밍 가격 변동 조건
- TC-4-1: 여러 코인 순차 매매
- TC-5-2: 수익률 낮은 코인 교체
- TC-6-3: 가격 콜백 실패 처리

### P3 (Medium - 일반)
- TC-1-3: 빈 후보 처리
- TC-2-3: "weak" 신호 매매 안 함
- TC-3-4: watch 타이밍 저위험 자동 매수
- TC-3-6: wait 타이밍 고위험 조건
- TC-3-7: wait 타이밍 저위험 진입 금지
- TC-4-2: `run_once`에서 모든 코인 체크
- TC-5-1: 최저 수익률 코인 찾기
- TC-5-3: 모든 코인 수익 시 교체 안 함
- TC-6-4: 캔들 조회 실패
- TC-6-5: 모니터링 스레드 중복 시작 방지
- TC-7-1 ~ TC-7-5: 엣지 케이스

---

## 6. 누락될 수 있는 리스크 요소

### 1. 동시성 문제
- **위험**: 여러 스레드에서 동시에 `final_candidates` 업데이트
- **위험**: 모니터링 루프와 매매 실행 간 경쟁 조건
- **해결**: `lock` 사용 확인 필요

### 2. 메모리 누수
- **위험**: 가격 히스토리 무한 증가 (`deque` `maxlen` 확인)
- **위험**: 모니터링 스레드 종료 안 됨
- **해결**: `maxlen` 설정, `stop_monitoring` 호출 확인

### 3. API 호출 제한
- **위험**: Upbit API rate limit 초과
- **위험**: 동적 모니터링에서 과도한 가격 조회
- **해결**: 호출 빈도 제한, 재시도 로직

### 4. 데이터 일관성
- **위험**: `final_candidates` 업데이트 중 매매 실행
- **위험**: 가격 히스토리와 실제 가격 불일치
- **해결**: 트랜잭션 처리, 타임스탬프 확인

### 5. 네트워크 장애
- **위험**: 가격 조회 실패 시 재시도 없음
- **위험**: Ollama 서버 연결 끊김
- **해결**: 재시도 로직, fallback 메커니즘

### 6. 포트폴리오 제한
- **위험**: 최대 보유 코인 수 초과
- **위험**: 교체 시 포트폴리오 크기 확인 안 함
- **해결**: 최대 보유 수 체크

### 7. 점수 계산 오류
- **위험**: `score_eff` 계산 실패 시 기본값 사용
- **위험**: 음수 점수 처리
- **해결**: 유효성 검증, 기본값 처리

### 8. 시간 동기화
- **위험**: 서버 시간과 로컬 시간 불일치
- **위험**: 모니터링 시작 시간 기록 오류
- **해결**: UTC 사용, 타임스탬프 검증

---

## 7. 테스트 실행 방법

```bash
# 전체 테스트 실행
pytest tests/test_final_candidates_trading.py -v

# P1 우선순위 테스트만 실행
pytest tests/test_final_candidates_trading.py::TestFinalCandidatesSelectionImpl -v

# 특정 테스트 케이스 실행
pytest tests/test_final_candidates_trading.py::TestImmediateTradingImpl::test_now_timing_strong_signal_immediate_buy -v

# 커버리지 확인
pytest tests/test_final_candidates_trading.py --cov=upbit_bot.services.execution --cov=upbit_bot.services.dynamic_monitor
```

