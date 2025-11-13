# 🚀 최신 업데이트 (2025-11-13)

## 📊 주요 기능 추가

### 1️⃣ AI 시장 분석 전략 (🤖 AIMarketAnalyzer)
- **파일**: `upbit_bot/strategies/ai_market_analyzer.py`
- **방식**: 로컬 Ollama AI (100.98.189.30:11434) 사용
- **모델**: qwen2.5-coder:7b (빠르고 정확함)
- **분석 주기**: **1분** (실시간에 가까운 분석)
- **비용**: ₩0 (로컬 실행, 비용 없음)

**작동 원리**:
```
매 1분마다:
1. 최근 1분 캔들 200개 수집
2. 기술 지표 계산 (MA, 변동성, 거래량)
3. Ollama AI에 시장 데이터 전송
4. AI가 신뢰도 기반 신호 생성 (BUY/SELL/HOLD)
5. 신뢰도 60% 이상일 때만 실행
```

---

### 2️⃣ 성과 분석 & 이익률 추적 (📊 PerformanceTracker)
- **파일**: `upbit_bot/data/performance_tracker.py`
- **저장소**: SQLite 데이터베이스 (`data/performance.db`)

**추적 지표**:
- ✅ 총 거래 수
- ✅ 승리/손실 거래 수
- ✅ 승률 (%)
- ✅ 총 수익/손실 (KRW)
- ✅ 평균 수익/손실
- ✅ 수익 팩터 (Profit Factor)
- ✅ 최대낙폭 (MDD - Maximum Drawdown)
- ✅ Sharpe Ratio
- ✅ 평균 거래 지속 시간
- ✅ 일일 성과 분석

**API 엔드포인트**:
```
GET  /performance?strategy=ai_market_analyzer&days=30
POST /record-trade
```

---

### 3️⃣ 향상된 서버 제어 UI (🎮)
**개선 사항**:
- 📍 실시간 서버 상태 표시 (🟢 Running / 🔴 Stopped)
- 🔘 시작/중지 버튼 (웹에서 제어 가능)
- 📊 거래 모드 표시 (Dry-run / Live)
- ⏱️ 마지막 실행 시간 (3초마다 업데이트)
- 🎯 마지막 신호 표시 (BUY/SELL/HOLD)

---

### 4️⃣ 거래 불가능 코인 필터링
- **문제**: 거래 불가능한 코인(LUNC, APENFT 등)이 자산 계산에 포함
- **해결**: 업비트 API에서 거래 가능한 마켓만 조회
- **결과**: 정확한 Crypto Value 계산

**Account Snapshot 개선**:
```
기존: Currency, Balance, Locked, Avg Buy
신규: Currency, Balance, Current Price, Valuation (KRW)
```

---

## 🔧 기술 사양

### 전략별 분석 주기
| 전략 | 캔들 단위 | 분석 주기 |
|------|----------|----------|
| AI 시장 분석 | 1분 | 60초 |
| 다른 전략 | 5분 | 300초 |

### 위험도 설정
- **포지션 사이징**: 전체 자산의 3%
- **최대 일일 손실**: 3%
- **최대 개수 포지션**: 3개

---

## 🌐 웹 대시보드 변경사항

### 새로운 섹션
1. **📊 성과 분석**
   - 기본 통계 (총 거래, 승률, 수익/손실)
   - 상세 지표 (Sharpe Ratio, 수익 팩터, MDD 등)

2. **🎮 서버 제어**
   - 서버 상태 실시간 표시
   - 모드 선택 (Dry-run/Live)
   - 시작/중지 버튼

### 업데이트 주기
- 서버 상태: **3초마다**
- 거래 내역/통계: **15초마다**
- 성과 분석: 거래 완료 시

---

## 📁 새로운 파일

```
upbit_bot/
├── data/
│   ├── performance_tracker.py      ← 성과 분석
│   └── trade_history.py            ← 거래 내역 저장
├── strategies/
│   ├── ai_market_analyzer.py        ← AI 전략 (새 파일)
│   ├── bb_squeeze.py                ← BB 스퀴즈 (추가)
│   ├── macd_crossover.py            ← MACD 교차 (추가)
│   ├── support_resistance.py         ← 지지/저항선 (추가)
│   └── volume_profile.py             ← 거래량 프로파일 (추가)
└── web/
    └── app.py                       ← 개선된 대시보드
```

---

## 🚀 사용 방법

### 1. 웹 대시보드 접속
```
http://localhost:8080/  (또는 http://100.81.173.120:8080)
```

### 2. AI 전략 선택
```
Settings → Strategy → "🤖 AI 시장 분석" 선택
→ Update Settings 클릭
```

### 3. 서버 시작
```
서버 제어 섹션 → 거래 모드 선택 → 서버 시작 클릭
```

### 4. 성과 추적
```
성과 분석 섹션에서 실시간 지표 확인
```

---

## 💡 Ollama 모델

**현재 설정**: qwen2.5-coder:7b (7.6B)
- ✅ 빠른 응답 속도
- ✅ 1분 주기에 최적화
- ✅ 충분한 정확도

**대체 모델**: gpt-oss:120b-cloud (116.8B)
- ⚠️ 느린 응답 (1분 주기 부족)
- ✅ 더 높은 정확도
- 📌 추천하지 않음 (현재)

---

## 🎯 다음 단계

1. **라이브 거래 시작**
   - Dry-run 모드에서 검증 후
   - Live 모드로 전환

2. **성과 분석**
   - 일일 성과 추적
   - 전략별 비교 분석

3. **AI 신뢰도 조정**
   - 현재: 60% (기본값)
   - 낮춤: 더 많은 신호 (높은 리스크)
   - 올림: 더 적은 신호 (보수적)

---

## 📝 GitHub 업로드

✅ **커밋**: `ec25693`
```
✨ Major Feature Update: AI Market Analysis + Performance Tracking + Server Control UI
```

**저장소**: https://github.com/skymean00-ctrl/upbit-bot

---

## 🆘 문제 해결

### Ollama 연결 실패
```
✗ 문제: "Connection to 100.98.189.30 timed out"
✓ 해결: 노트북의 Ollama 서버 확인 (ollama serve 실행)
```

### 성과 데이터 없음
```
✗ 문제: 성과 분석이 빈 상태
✓ 해결: 거래 1회 이상 완료 필요
```

### 높은 CPU 사용률
```
✗ 문제: AI 분석 중 CPU 100%
✓ 해결: Ollama 모델 변경 (qwen2.5-coder:7b 유지 추천)
```

---

**마지막 업데이트**: 2025-11-13
**버전**: 2.1.0
**상태**: ✅ Production Ready

