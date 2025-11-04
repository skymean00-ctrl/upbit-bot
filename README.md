# Upbit Automated Trading Bot

Python 기반으로 작성된 업비트 자동 매매 봇입니다. 실거래 이전에 백테스트와 페이퍼 트레이딩으로 전략을 검증하는 것을 권장합니다.

## 주요 구성

- `upbit_bot/config`: 환경설정 및 시크릿 관리.
- `upbit_bot/core`: 업비트 REST/WebSocket 클라이언트, 주문 및 계좌 모듈.
- `upbit_bot/strategies`: 전략 구현체와 공통 인터페이스.
- `upbit_bot/services`: 실행 엔진, 리스크 관리, 백테스트 도구.
- `scripts`: 실행/유틸리티 스크립트.
- `tests`: 단위 테스트 및 통합 테스트.

## 개발 시작하기

1. **Python 버전**: 3.11 이상을 권장합니다.
2. **가상환경**: `python -m venv .venv` 후 `source .venv/bin/activate`.
3. **의존성 설치**:

   ```bash
   pip install -r requirements.txt
   # 또는 개발 환경에서는
   pip install -r requirements-dev.txt
   ```
4. **환경 변수 설정**:

   ```bash
   cp .env.example .env
   # .env 파일에 다음 값을 입력합니다.
   # UPBIT_ACCESS_KEY=...
   # UPBIT_SECRET_KEY=...
   ```

5. **연결 테스트**:

   ```bash
   python scripts/test_connection.py
   ```

6. **전략 실행 (드라이런)**:

   ```bash
   python scripts/run_bot.py --once
   ```

   실거래 시에는 `--live` 옵션과 `--order-amount`(매수 KRW 또는 매도 수량) 값을 지정하세요.
   모든 주문 금액은 최소 5,000 KRW 이상으로 자동 보정됩니다.

7. **데이터 수집**:

   ```bash
   python scripts/collect_data.py --markets KRW-BTC KRW-ETH
   ```

   실시간 체결·호가가 SQLite(`data/upbit_marketdata.db`)에 적재됩니다.

 ## 테스트

```bash
pytest
```

## 추가 권장사항

- 실시간 체결·호가를 저장하는 데이터 파이프라인을 구축해 향후 백테스트와 고급 지표에 활용하세요.
- `scripts/backtest.py`와 파라미터 스윕 도구를 추가해 워크포워드 테스트 및 성과 리포트를 자동화하는 것이 좋습니다.
- 일일 손실 한도, 슬리피지 고려, 주문 재시도 등 리스크 관리 로직을 별도 모듈로 분리해 안정성을 높이세요.
- 텔레그램/슬랙 알림이나 Grafana 대시보드로 모니터링을 붙이면 운영 상태를 실시간으로 확인할 수 있습니다.
- 서로 다른 전략(모멘텀·평균회귀 등)을 포트폴리오로 구성하고 자금 배분 규칙을 적용하면 장세 변화에 대응하기 유리합니다.
- 타입 힌트, `ruff`/`black` 같은 린트·포맷 도구, CI 파이프라인을 도입해 코드 품질과 일관성을 유지하세요.

## 스크립트 개요

- `scripts/run_bot.py`: 리스크 관리, 포지션 사이징, 슬랙/텔레그램 알림을 지원하는 실거래/드라이런 실행기 (기본 이동평균은 14/37 설정으로 최근 KRW-BTC 200분 데이터에서 약 66% 승률 확인).
- `scripts/collect_data.py`: 업비트 웹소켓 체결·호가를 받아 SQLite에 저장.
- `scripts/backtest.py`: CSV 기반 백테스트 및 결과 JSON 출력.
- `scripts/test_connection.py`: API 키 유효성 검사.
- `scripts/web_dashboard.py`: FastAPI 대시보드로 상태 확인, 시작/정지, 계좌 조회를 지원.

## 전략/리스크/알림 설정

- `.env` 파일에서 `RISK_MAX_*` 값으로 손실 한도와 포지션 규모를 제어합니다.
- 슬랙 웹훅 또는 텔레그램 봇 토큰/채팅 ID를 지정하면 거래 이벤트 알림을 받을 수 있습니다.
- `UPBIT_STRATEGY=composite`로 설정하고 실행 시 다음과 같은 JSON을 전달하면 다중 전략을 가중합으로 운용할 수 있습니다.

  ```bash
  python scripts/run_bot.py --once \
    --env-file .env \
    --components '[{"name":"ma_crossover","weight":0.7,"params":{"short_window":5,"long_window":20}},{"name":"ma_crossover","weight":0.3,"params":{"short_window":10,"long_window":40}}]'
  ```

## 품질 관리

- `pip install -r requirements-dev.txt` 후 아래 명령으로 코드 품질을 검사하세요.

  ```bash
  ruff check .
  black --check .
  mypy upbit_bot
  pytest
  ```

- GitHub Actions 워크플로(`.github/workflows/ci.yml`)가 자동으로 린트/타입체크/pytest를 수행합니다.

## 보안 주의사항

- API 키는 절대 깃 저장소나 공유 저장소에 커밋하지 마세요.
- 제한된 권한 키(조회/거래 분리)를 사용하고, IP 허용 리스트를 관리하세요.
- 실거래 전 충분한 테스트를 수행하세요.

## 웹 대시보드 실행

FastAPI 기반 대시보드에서 실행 상태, 최근 시그널, 계좌 잔액을 확인하고 봇을 시작/정지할 수 있습니다.

```bash
python scripts/web_dashboard.py --host 0.0.0.0 --port 8000
```

브라우저에서 `http://<host>:<port>/`로 접속하면 현재 상태가 15초마다 자동 갱신되며, 드라이런/라이브 모드를 선택해 실행할 수 있습니다. 기본값은 드라이런입니다.
