# upbit-bot

업비트 자동매매 봇 프로젝트의 기본 개념과 운영 방법을 요약한 문서입니다. 로컬 개발 환경을 준비하고, 전략을 구성하며, 운영 환경으로 배포할 때 아래 가이드를 참고하세요.

## 필수 조건
- **Python**: 3.10 이상. 가상환경 사용을 권장합니다.
- **패키지 관리 도구**: `pip` 혹은 `pipx`.
- **핵심 라이브러리**:
  - [`requests`](https://docs.python-requests.org/): 업비트 REST API 호출.
  - [`python-dotenv`](https://saurabh-kumar.com/python-dotenv/): `.env` 파일 기반 환경변수 로딩.
  - [`pandas`](https://pandas.pydata.org/) & [`ta`](https://technical-analysis-library-in-python.readthedocs.io/): 시계열 처리 및 보조지표 계산.
  - [`websockets`](https://websockets.readthedocs.io/): 시세 스트리밍 구독 시 활용.
- **선택 사항**: `poetry` 또는 `pip-tools` 같은 의존성 고정 도구, Docker.

필요 라이브러리는 아래 명령으로 일괄 설치할 수 있습니다.

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install requests python-dotenv pandas ta websockets
```

## 환경 변수 설정
`.env` 파일을 사용해 민감 정보를 안전하게 주입합니다. 프로젝트 루트에 있는 [`\.env.example`](./.env.example)를 복사해 `.env` 파일을 만들고 값을 채워 주세요.

```bash
cp .env.example .env
```

주요 항목은 다음과 같습니다.

| 변수 | 설명 |
| --- | --- |
| `UPBIT_API_KEY` | 업비트 API 키의 Access Key. |
| `UPBIT_SECRET_KEY` | 업비트 API Secret Key. 절대 저장소에 커밋하지 않습니다. |
| `UPBIT_MARKET` | 거래할 마켓 심볼 (예: `KRW-BTC`). |
| `POSITION_SIZE` | 단일 포지션 진입 금액 혹은 비중. |
| `MAX_POSITIONS` | 동시에 보유할 수 있는 포지션 수. |
| `SLACK_WEBHOOK_URL` | 선택 사항. 체결/에러 알림용 Slack Webhook 주소. |

`.env`는 `python-dotenv` 혹은 Docker/배포 환경의 Secret 관리 시스템에서 자동으로 로드되도록 구성합니다.

## 실행 방법
1. **환경 준비**
   - 가상환경을 생성하고 필수 라이브러리를 설치합니다.
   - `.env` 파일을 준비해 인증정보와 위험관리 파라미터를 채웁니다.
2. **백테스트 실행**
   - CLI 예시: `python -m bot.cli backtest --strategy ma-cross --market KRW-BTC --start 2022-01-01 --end 2023-01-01`.
   - 백테스트 결과를 통해 파라미터(`fast_window`, `slow_window`, `take_profit`, `stop_loss`)를 조정합니다.
3. **실거래/페이퍼 트레이딩 실행**
   - CLI 예시: `python -m bot.cli trade --strategy ma-cross --mode paper` (페이퍼) 또는 `--mode live`.
   - 서비스형 운영 시 프로세스 관리자로 `python -m bot.service`를 실행하여 주문 루프, 모니터링 루프를 병렬로 띄웁니다.
4. **모니터링 및 종료**
   - `systemctl status upbit-bot` 혹은 `pm2 status upbit-bot`으로 상태를 확인합니다.
   - 종료 시에는 `CTRL+C` 또는 프로세스 매니저 명령을 사용합니다.

## 전략 개요
가장 기본 전략은 **이동평균 크로스(Moving Average Crossover)** 입니다.

- `fast_window`와 `slow_window`의 단순 이동평균(SMA)을 계산합니다.
- `SMA_fast`가 `SMA_slow`를 상향 돌파하면 매수 진입, 하향 돌파하면 매도/청산합니다.
- 변동성 필터(예: ATR)와 거래량 필터를 추가해 노이즈를 줄입니다.
- 손절/익절 비율, 트레일링 스탑, 최대 동시 포지션 수로 위험을 제어합니다.

자세한 구조와 위험관리, 배포 파이프라인은 [`docs/architecture.md`](./docs/architecture.md)에서 확인할 수 있습니다.
