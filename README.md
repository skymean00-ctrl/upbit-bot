# upbit-bot

간단한 업비트 자동매매 실험 프로젝트입니다. 다음과 같은 구성 요소를 제공합니다.

## 백테스팅
- `backtesting/engine.py`: 단일 포지션 기반 체결 로직과 수수료를 고려한 시뮬레이터
- `tests/data/test_prices.csv`: 샘플 시세 데이터
- `pytest -q`: 모멘텀/평균회귀/돌파 전략별 검증 케이스

## 실거래 로깅
- `upbit_bot/live/logging.py`: 체결, 손익, 오류를 구조화된 JSON으로 기록
- `upbit_bot/live/notifications.py`: 슬랙 웹훅 및 이메일 발송 모듈과 일괄 알림 매니저

## 개발 환경
- `pyproject.toml`: 테스트(`pytest`)와 린트(`ruff`) 의존성 명시
- `.github/workflows/ci.yml`: Pull Request마다 자동으로 테스트와 린트 실행
