# upbit-bot

Upbit 자동매매 예제를 위한 프로젝트입니다. REST/WebSocket 기반 클라이언트, 전략, 리스크 관리, 실행기, 그리고 CLI 루프를 포함합니다.

## 구조

```
src/
  api/upbit_client.py         # REST/WebSocket 클라이언트
  data/collector.py           # 시세 수집
  strategies/                 # 전략 (이동평균 등)
  risk/position_manager.py    # 포지션 및 리스크 제어
  executor/trade_executor.py  # 전략 + 리스크 + 주문 실행
main.py                       # 설정 로딩 및 이벤트 루프
```

## 실행 방법

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt  # 필요한 경우 websockets, requests 등을 설치
python main.py --config config.json --verbose
```

`config.json` 예시는 다음과 같습니다.

```json
{
  "access_key": "YOUR_ACCESS_KEY",
  "secret_key": "YOUR_SECRET_KEY",
  "market": "KRW-BTC",
  "candle_interval": "minutes/1",
  "order_amount": 10000,
  "poll_interval": 5,
  "strategy": {
    "moving_average": {
      "short_window": 5,
      "long_window": 20
    }
  },
  "risk": {
    "max_position": 0.001,
    "max_notional": 500000
  }
}
```

실제 주문을 제출하기 전에 sandbox나 모의 환경에서 충분히 검증하세요.
