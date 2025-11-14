# Windows 노트북 Ollama 빠른 설정 가이드

## 현재 상황
- **웹서버**: Linux 서버 (192.168.1.70:8080)
- **Ollama**: Windows 노트북에 설치됨
- **연결**: Linux 서버 → Windows 노트북 Ollama

## 1단계: Windows 노트북 IP 주소 확인

### Windows PowerShell에서 실행:
```powershell
# IP 주소 확인
ipconfig

# 또는 더 자세히
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.InterfaceAlias -notlike "*Loopback*"} | Select-Object IPAddress, InterfaceAlias
```

일반적으로 `192.168.x.x` 또는 `10.x.x.x` 형태의 IP 주소를 찾으세요.

## 2단계: Windows 노트북에서 Ollama 외부 접근 허용

### A. Windows 방화벽 설정

**PowerShell (관리자 권한)에서 실행:**
```powershell
New-NetFirewallRule -DisplayName "Ollama" -Direction Inbound -LocalPort 11434 -Protocol TCP -Action Allow
```

### B. Ollama가 외부 접근 허용하도록 설정

**PowerShell에서 실행:**
```powershell
# 환경 변수 설정 (사용자 레벨)
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "User")

# 또는 시스템 레벨 (관리자 권한 필요)
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "Machine")
```

**설정 후 Ollama 재시작:**
```powershell
# Ollama 종료 후 재시작
ollama serve
```

## 3단계: Linux 서버에서 Windows 노트북 연결 설정

### 방법 1: 환경 변수로 설정 (권장)

```bash
# Linux 서버에서 실행
export OLLAMA_BASE_URL="http://WINDOWS_NOTEBOOK_IP:11434"
export OLLAMA_MODEL="llama3.1:8b"

# 예시 (Windows 노트북 IP가 192.168.1.100인 경우)
export OLLAMA_BASE_URL="http://192.168.1.100:11434"
export OLLAMA_MODEL="llama3.1:8b"
```

### 방법 2: .env 파일에 추가

프로젝트 루트에 `.env` 파일 생성 또는 수정:
```bash
OLLAMA_BASE_URL=http://WINDOWS_NOTEBOOK_IP:11434
OLLAMA_MODEL=llama3.1:8b
```

### 방법 3: 코드에서 직접 설정

`upbit_bot/strategies/ai_market_analyzer.py` 파일 수정:
```python
OLLAMA_BASE_URL = "http://WINDOWS_NOTEBOOK_IP:11434"  # Windows 노트북 IP
OLLAMA_MODEL = "llama3.1:8b"
```

## 4단계: 연결 테스트

### Windows 노트북에서:
```powershell
# 로컬 테스트
curl http://localhost:11434/api/tags
```

### Linux 서버에서:
```bash
# Windows 노트북 IP로 테스트 (예: 192.168.1.100)
./scripts/test_windows_ollama.sh 192.168.1.100

# 또는 수동 테스트
curl http://WINDOWS_NOTEBOOK_IP:11434/api/tags
```

## 5단계: 웹서버 재시작

환경 변수 설정 후:
```bash
# 웹서버 재시작
cd /home/skymean00/projects/upbit-bot
ps aux | grep web_dashboard | grep -v grep | awk '{print $2}' | xargs kill -15
sleep 2
PYTHONPATH=/home/skymean00/projects/upbit-bot:$PYTHONPATH nohup python3 scripts/web_dashboard.py --host 0.0.0.0 --port 8080 > logs/web_server.log 2>&1 &
```

## 체크리스트

- [ ] Windows 노트북 IP 주소 확인
- [ ] Windows 방화벽에서 포트 11434 열기
- [ ] Ollama 환경 변수 `OLLAMA_HOST=0.0.0.0:11434` 설정
- [ ] Ollama 재시작
- [ ] Windows 노트북에서 로컬 테스트 성공
- [ ] Linux 서버에서 Windows 노트북으로 연결 테스트
- [ ] 환경 변수 또는 코드 설정
- [ ] 웹서버 재시작

## 문제 해결

### 연결이 안 되는 경우:
1. **Windows 노트북 IP 확인**: `ipconfig` 실행
2. **방화벽 확인**: 포트 11434 열려 있는지 확인
3. **Ollama 실행 확인**: `ollama serve` 실행 중인지 확인
4. **네트워크 연결 확인**: 같은 네트워크인지 확인 (같은 Wi-Fi/유선)

### 모델을 찾을 수 없는 경우:
Windows 노트북에서:
```powershell
ollama pull llama3.1:8b
ollama pull deepseek-r1:7b
```

