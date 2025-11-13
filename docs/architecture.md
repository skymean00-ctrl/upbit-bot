# Architecture & Operations

이 문서는 업비트 자동매매 봇의 구성 요소, 위험 관리, 배포 플로우를 상세히 설명합니다. 실제 구현 세부는 팀 상황에 맞춰 조정할 수 있습니다.

## 1. 시스템 구성 요소

```text
┌──────────────────────────┐
│        CLI / API         │  (bot.cli, FastAPI adapters)
└────────────┬─────────────┘
             │ commands & configs
┌────────────▼─────────────┐
│    Strategy Engine       │  (signals, state machine)
├────────────┬─────────────┤
│ Risk Layer │ Order Layer │
└─────┬──────┴──────┬──────┘
      │             │
┌─────▼──────┐ ┌────▼──────┐
│Data Feeds  │ │Execution  │
│(REST/WS)   │ │Adapters   │
└─────┬──────┘ └────┬──────┘
      │             │
┌─────▼─────────────▼──────┐
│ Persistence & Monitoring │ (DB, Prometheus, Slack)
└──────────────────────────┘
```

### Strategy Engine
- **Signal Layer**: 이동평균 크로스, 모멘텀, 변동성 돌파 등의 지표 기반 시그널 계산.
- **State Machine**: 포지션 상태(진입, 청산, 대기)를 추적하며 재진입 조건을 평가.
- **Parameter Store**: YAML/JSON 설정파일, 환경변수, 실시간 튜닝 API를 통해 파라미터를 주입.

### Data Feeds
- REST: 캔들/계좌정보 조회. 2초당 30회 제한을 고려해 `asyncio` 큐로 rate limit을 관리.
- WebSocket: 틱 데이터 및 호가 데이터를 구독해 슬리피지를 줄입니다.

### Execution Layer
- 주문 요청 서명, 응답 검증, 재시도/백오프를 담당.
- 주문 결과를 이벤트 버스로 브로드캐스트하여 리스크 레이어와 모니터링이 동기화되도록 합니다.

### Persistence & Monitoring
- 주문 로그/체결 로그는 PostgreSQL 혹은 SQLite에 적재.
- 메트릭은 Prometheus + Grafana, 알림은 Slack/Telegram Webhook으로 전파.

## 2. 위험 관리

| 위험 항목 | 제어 방법 |
| --- | --- |
| 시장 급변 | ATR 기반 동적 손절, 변동성 필터로 거래 중단. |
| 주문 실패 | 재시도 + 회로차단기(circuit breaker)로 API 오류를 완화. |
| API 키 유출 | `.env` + Secret Manager, 최소 권한 Access Key. |
| 포지션 과다 | `POSITION_SIZE`, `MAX_POSITIONS`, 잔고 체크로 한도 관리. |
| 네트워크 장애 | `asyncio.wait_for` 타임아웃, 멱등성 키로 중복 주문 방지. |

추가적으로, 슬리피지 한도 및 거래 정지 스케줄을 설정해 특정 이벤트(점검, FOMC 등) 동안 자동매매를 비활성화합니다.

## 3. 배포 흐름

1. **개발 & 테스트**
   - 로컬 가상환경에서 전략을 구현하고 `pytest`/`backtest` CLI로 검증.
   - `pre-commit` 훅으로 포맷/정적분석을 실행.
2. **CI 파이프라인**
   - GitHub Actions 혹은 GitLab CI에서 테스트, 린트, 도커 이미지 빌드를 수행.
   - 성공 시 `main` 브랜치에 머지하고 태그 생성.
3. **배포**
   - Docker 이미지 푸시 → Kubernetes CronJob 혹은 장기 실행 Deployment에 롤링 업데이트.
   - Secrets는 KMS/SealedSecrets로 주입.
   - 헬스체크 엔드포인트(`/healthz`)로 상태를 확인하고, 실패 시 자동 재시작.
4. **운영**
   - Grafana 대시보드에서 PnL, 승률, 실패율을 모니터링.
   - Slack 경보를 통해 오류 발생 시 즉시 대응.

## 4. 로드맵 제안
- 전략 템플릿 시스템화: `strategy/ma_cross.py`, `strategy/momentum.py` 등 모듈화.
- 이벤트 구동 아키텍처: `asyncio` Queue 또는 Kafka로 시그널-주문 decoupling.
- 자동 파라미터 튜닝: Bayesian Optimization 연계.
- 리스크 대시보드: Streamlit 또는 FastAPI Admin을 통해 실시간 노출.
