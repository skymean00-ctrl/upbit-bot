# Upbit Bot 웹 서버 설정 상태

## 현재 상황

1. **포트 8000**: 다른 서버가 실행 중 (PID 50351, upbit-bot이 아님)
2. **포트 8080**: upbit-bot 서버가 실행 중이 아님
3. **Tailscale IP**: 100.81.173.120

## 문제점

- upbit-bot 웹 서버가 실행되지 않음
- 포트 8080에서 서버가 실행 중이 아니어서 웹페이지 접속 불가

## 해결 방법

### 1. 서버 실행

```bash
cd /home/skymean00/projects/upbit-bot
source venv/bin/activate
python scripts/web_dashboard.py --host 0.0.0.0 --port 8080
```

또는 제공된 스크립트 사용:

```bash
cd /home/skymean00/projects/upbit-bot
./start_web_server.sh
```

### 2. 접속 주소

서버 실행 후 다음 주소로 접속:
- 로컬: http://localhost:8080
- Tailscale: http://100.81.173.120:8080

### 3. 백그라운드 실행 (선택사항)

```bash
cd /home/skymean00/projects/upbit-bot
nohup ./start_web_server.sh > logs/web_server.log 2>&1 &
```

### 4. 포트 변경이 필요한 경우

포트 8000을 사용하고 싶다면:
- 기존 서버(PID 50351)를 중지하거나
- 다른 포트(예: 8080, 8888)를 사용

## 확인 사항

✅ 서버는 `0.0.0.0`으로 바인딩되어 Tailscale 접속 가능
✅ `load_settings()` 함수가 `env_path` 파라미터 지원
✅ FastAPI 앱이 올바르게 생성됨

## 다음 단계

1. 서버를 실행하세요
2. http://100.81.173.120:8080 으로 접속해보세요
3. 접속이 안 되면 방화벽 설정을 확인하세요

