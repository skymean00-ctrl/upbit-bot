# Windows 노트북 Ollama 설정 가이드

## 현재 상황
- **Ollama 설치 위치**: Windows 노트북
- **웹서버 실행 위치**: Linux 서버 (100.81.173.120:8080)
- **연결 방법**: Linux 서버에서 Windows 노트북의 Ollama로 네트워크 연결

## Windows 노트북 설정

### 1. Ollama 설치 확인
Windows 노트북에서 다음 명령어 실행:
```powershell
# PowerShell에서 실행
ollama --version
ollama list
```

### 2. Ollama 서비스 확인
Windows 노트북에서 Ollama가 실행 중인지 확인:
```powershell
# Ollama 실행 확인 (기본 포트: 11434)
Test-NetConnection -ComputerName localhost -Port 11434
```

### 3. 네트워크 접근 허용 설정

#### A. Windows 방화벽 설정
1. **제어판** → **시스템 및 보안** → **Windows Defender 방화벽**
2. **고급 설정** 클릭
3. **인바운드 규칙** → **새 규칙** 클릭
4. 규칙 유형: **포트** 선택
5. TCP, 포트: **11434** 선택
6. 연결 허용 선택
7. 프로필: **도메인, 개인, 공용** 모두 선택
8. 이름: "Ollama" 입력

또는 PowerShell에서:
```powershell
# 관리자 권한으로 실행
New-NetFirewallRule -DisplayName "Ollama" -Direction Inbound -LocalPort 11434 -Protocol TCP -Action Allow
```

#### B. Ollama가 모든 네트워크 인터페이스에 바인딩되도록 설정

Ollama 기본 설정은 localhost에만 바인딩됩니다. 외부 접근을 허용하려면:

**방법 1: 환경 변수 설정 (권장)**
```powershell
# 시스템 환경 변수 추가
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "Machine")

# 또는 사용자 환경 변수
[Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "User")
```

설정 후 Ollama 재시작 필요.

**방법 2: Ollama 서비스 시작 시 옵션 지정**
```powershell
# 환경 변수 설정 후 Ollama 재시작
$env:OLLAMA_HOST="0.0.0.0:11434"
ollama serve
```

### 4. Windows 노트북 IP 주소 확인
```powershell
# IP 주소 확인
ipconfig

# 또는
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.InterfaceAlias -notlike "*Loopback*"} | Select-Object IPAddress, InterfaceAlias
```

일반적으로:
- **유선 네트워크**: `192.168.x.x` 또는 `10.x.x.x`
- **Wi-Fi**: `192.168.x.x` 또는 `10.x.x.x`

### 5. Linux 서버에서 Windows 노트북 연결 테스트
```bash
# Windows 노트북의 IP 주소를 확인한 후 (예: 192.168.1.100)
curl http://192.168.1.100:11434/api/tags

# 또는 ping 테스트
ping -c 3 192.168.1.100
```

## 코드 설정

### 방법 1: 환경 변수로 설정 (권장)
Linux 서버에서 `.env` 파일 또는 환경 변수 설정:
```bash
export OLLAMA_BASE_URL="http://WINDOWS_NOTEBOOK_IP:11434"
export OLLAMA_MODEL="llama3.1:8b"
```

### 방법 2: 코드에서 직접 설정
```python
# upbit_bot/strategies/ai_market_analyzer.py
OLLAMA_BASE_URL = "http://192.168.1.100:11434"  # Windows 노트북 IP
OLLAMA_MODEL = "llama3.1:8b"
```

## 연결 테스트

### Windows 노트북에서 테스트
```powershell
# 로컬 테스트
curl http://localhost:11434/api/tags

# 또는
Invoke-WebRequest -Uri "http://localhost:11434/api/tags" | Select-Object -ExpandProperty Content
```

### Linux 서버에서 테스트
```bash
# Windows 노트북 IP로 테스트 (예: 192.168.1.100)
curl http://192.168.1.100:11434/api/tags

# 모델 목록 확인
curl http://192.168.1.100:11434/api/tags | python3 -m json.tool
```

## 트러블슈팅

### 문제 1: 연결 거부됨
**해결책**:
1. Windows 방화벽에서 포트 11434 열기
2. Ollama가 `0.0.0.0:11434`에 바인딩되었는지 확인
3. Windows 노트북과 Linux 서버가 같은 네트워크에 있는지 확인

### 문제 2: 타임아웃
**해결책**:
1. Windows 노트북 IP 주소 확인
2. 네트워크 연결 상태 확인
3. Ollama 서비스가 실행 중인지 확인

### 문제 3: 모델을 찾을 수 없음
**해결책**:
```powershell
# Windows 노트북에서 모델 설치 확인
ollama list

# 모델 설치 (아직 설치되지 않은 경우)
ollama pull llama3.1:8b
ollama pull deepseek-r1:7b
```

## 빠른 설정 체크리스트

- [ ] Windows 노트북에서 Ollama 설치 확인
- [ ] Windows 방화벽에서 포트 11434 열기
- [ ] Ollama 환경 변수 `OLLAMA_HOST=0.0.0.0:11434` 설정
- [ ] Windows 노트북 IP 주소 확인
- [ ] Linux 서버에서 Windows 노트북으로 연결 테스트
- [ ] 코드에서 `OLLAMA_BASE_URL`을 Windows 노트북 IP로 설정
- [ ] 필요한 모델 설치 (llama3.1:8b, deepseek-r1:7b)

## 현재 설정 예시

```
Windows 노트북 IP: 192.168.1.100
Ollama 포트: 11434
설치된 모델: llama3.1:8b, deepseek-r1:7b

Linux 서버 코드 설정:
OLLAMA_BASE_URL = "http://192.168.1.100:11434"
OLLAMA_MODEL = "llama3.1:8b"
```

