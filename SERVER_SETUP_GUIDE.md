# Upbit Bot 웹 서버 설정 가이드

## ✅ 현재 상태

**서버가 정상적으로 실행 중입니다!**

- **포트**: 8080
- **호스트**: 0.0.0.0 (모든 인터페이스에서 접속 가능)
- **Tailscale IP**: 100.81.173.120
- **접속 주소**: http://100.81.173.120:8080

## 📋 접속 방법

### 1. 로컬에서 접속
```bash
http://localhost:8080
```

### 2. Tailscale을 통해 접속
```bash
http://100.81.173.120:8080
```

## 🚀 서버 실행 방법

### 방법 1: 직접 실행
```bash
cd /home/skymean00/projects/upbit-bot
source venv/bin/activate
python scripts/web_dashboard.py --host 0.0.0.0 --port 8080
```

### 방법 2: 제공된 스크립트 사용
```bash
cd /home/skymean00/projects/upbit-bot
./start_web_server.sh
```

### 방법 3: 백그라운드 실행
```bash
cd /home/skymean00/projects/upbit-bot
source venv/bin/activate
nohup python scripts/web_dashboard.py --host 0.0.0.0 --port 8080 > logs/web_server.log 2>&1 &
```

## 🔧 설정 확인 사항

### ✅ 완료된 설정
1. `load_settings()` 함수에 `env_path` 파라미터 추가
2. `web_dashboard.py`에서 설정 로드 수정
3. 서버가 `0.0.0.0`으로 바인딩되어 Tailscale 접속 가능
4. 포트 8080에서 정상 실행 확인

### ⚠️ 주의사항
- 포트 8000에는 다른 서버가 실행 중입니다 (PID 50351)
- upbit-bot은 포트 8080에서 실행됩니다
- 포트 8000을 사용하려면 기존 서버를 중지해야 합니다

## 📝 서버 관리

### 서버 중지
```bash
# 프로세스 ID 확인
ps aux | grep web_dashboard

# 프로세스 종료
kill <PID>
```

### 서버 재시작
```bash
# 기존 프로세스 종료 후
cd /home/skymean00/projects/upbit-bot
./start_web_server.sh
```

## 🌐 Tailscale 설정

Tailscale IP: **100.81.173.120**

서버가 `0.0.0.0`으로 바인딩되어 있으므로 Tailscale 네트워크를 통해 접속할 수 있습니다.

## 📊 웹 대시보드 기능

- 봇 상태 확인 (실행 중/중지됨)
- 드라이런/라이브 모드 전환
- 계좌 잔액 확인
- 최근 주문 내역 확인
- 자동 새로고침 (15초마다)

## 🔍 문제 해결

### 접속이 안 될 경우

1. **서버가 실행 중인지 확인**
   ```bash
   ps aux | grep web_dashboard
   netstat -tlnp | grep 8080
   ```

2. **포트가 열려있는지 확인**
   ```bash
   curl http://localhost:8080
   curl http://100.81.173.120:8080
   ```

3. **방화벽 설정 확인**
   ```bash
   sudo ufw status
   # 필요시 포트 8080 열기
   sudo ufw allow 8080
   ```

4. **로그 확인**
   ```bash
   tail -f /tmp/upbit_bot_server.log
   # 또는
   tail -f logs/web_server.log
   ```

## 📞 추가 도움말

문제가 지속되면 다음을 확인하세요:
- `.env` 파일에 올바른 API 키가 설정되어 있는지
- 가상환경이 활성화되어 있는지
- 필요한 패키지가 설치되어 있는지 (`pip install -r requirements.txt`)

