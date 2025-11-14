# Ollama 노트북 설치 가이드

## 1. Ollama 설치

### Linux (Ubuntu/Debian)
```bash
# Ollama 다운로드 및 설치
curl -fsSL https://ollama.com/install.sh | sh

# 또는 직접 설치
curl -L https://ollama.com/download/ollama-linux-amd64 -o /tmp/ollama
chmod +x /tmp/ollama
sudo mv /tmp/ollama /usr/local/bin/

# Ollama 서비스 시작
ollama serve
```

### macOS
```bash
# Homebrew를 사용한 설치
brew install ollama

# 또는 공식 인스톨러 다운로드
# https://ollama.com/download/mac
```

### Windows
1. 공식 웹사이트에서 다운로드: https://ollama.com/download/windows
2. 설치 파일 실행 및 설치 완료

## 2. Ollama 서비스 시작

### 백그라운드 실행 (Linux)
```bash
# Systemd 서비스로 실행 (추천)
sudo systemctl enable ollama
sudo systemctl start ollama

# 또는 직접 실행
ollama serve &

# 서비스 상태 확인
systemctl status ollama
```

### 포트 변경 (선택사항)
기본 포트는 11434입니다. 다른 포트를 사용하려면:
```bash
# 환경 변수 설정
export OLLAMA_HOST=0.0.0.0:11434

# 또는 systemd 서비스 파일 수정
sudo nano /etc/systemd/system/ollama.service
# Environment="OLLAMA_HOST=0.0.0.0:11434" 추가
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

## 3. 코인 거래 최적화 모델 설치

### 추천 모델 설치 스크립트

```bash
#!/bin/bash
# 코인 거래 최적화 Ollama 모델 설치 스크립트

echo "=========================================="
echo "코인 거래 최적화 Ollama 모델 설치 시작"
echo "=========================================="
echo ""

# 1. Llama 3.1 8B (가장 추천)
echo "1️⃣ Llama 3.1 8B 설치 중..."
ollama pull llama3.1:8b
if [ $? -eq 0 ]; then
    echo "✅ Llama 3.1 8B 설치 완료"
else
    echo "❌ Llama 3.1 8B 설치 실패"
fi
echo ""

# 2. DeepSeek-R1 7B (수치 분석 강점)
echo "2️⃣ DeepSeek-R1 7B 설치 중..."
ollama pull deepseek-r1:7b
if [ $? -eq 0 ]; then
    echo "✅ DeepSeek-R1 7B 설치 완료"
else
    echo "❌ DeepSeek-R1 7B 설치 실패"
fi
echo ""

# 3. Qwen2.5 7B (범용 버전)
echo "3️⃣ Qwen2.5 7B (범용) 설치 중..."
ollama pull qwen2.5:7b
if [ $? -eq 0 ]; then
    echo "✅ Qwen2.5 7B 설치 완료"
else
    echo "❌ Qwen2.5 7B 설치 실패"
fi
echo ""

# 4. Mistral 7B Instruct (선택사항)
echo "4️⃣ Mistral 7B Instruct 설치 중..."
ollama pull mistral:7b-instruct
if [ $? -eq 0 ]; then
    echo "✅ Mistral 7B Instruct 설치 완료"
else
    echo "❌ Mistral 7B Instruct 설치 실패"
fi
echo ""

echo "=========================================="
echo "설치 완료! 다음 모델을 사용할 수 있습니다:"
echo "=========================================="
echo "  📦 llama3.1:8b"
echo "  📦 deepseek-r1:7b"
echo "  📦 qwen2.5:7b"
echo "  📦 mistral:7b-instruct"
echo ""
echo "설치된 모델 확인:"
ollama list
```

### 개별 모델 설치

```bash
# Llama 3.1 8B (가장 추천)
ollama pull llama3.1:8b

# DeepSeek-R1 7B (수치 분석 강점)
ollama pull deepseek-r1:7b

# Qwen2.5 7B (범용)
ollama pull qwen2.5:7b

# Mistral 7B Instruct
ollama pull mistral:7b-instruct

# 더 큰 모델 (성능 우수, 느림)
ollama pull llama3.1:70b
ollama pull qwen2.5:14b
```

## 4. 설치 확인

```bash
# 설치된 모델 목록 확인
ollama list

# 모델 테스트
ollama run llama3.1:8b "암호화폐 거래에 대해 간단히 설명해줘"

# Ollama 서비스 상태 확인
curl http://localhost:11434/api/tags
```

## 5. 네트워크 설정 (원격 접근 허용)

### 방화벽 설정 (Linux)
```bash
# UFW 사용 시
sudo ufw allow 11434/tcp

# 또는 iptables 사용 시
sudo iptables -A INPUT -p tcp --dport 11434 -j ACCEPT
sudo iptables-save
```

### Ollama 호스트 바인딩 확인
```bash
# 환경 변수 확인
echo $OLLAMA_HOST

# 모든 인터페이스에 바인딩 (원격 접근 허용)
export OLLAMA_HOST=0.0.0.0:11434

# 서비스 재시작
sudo systemctl restart ollama
```

## 6. 코드에서 노트북 Ollama 사용 설정

### 현재 서버 설정
```python
# upbit_bot/strategies/ai_market_analyzer.py
OLLAMA_BASE_URL = "http://100.98.189.30:11434"  # 원격 서버
OLLAMA_MODEL = "qwen2.5-coder:7b"
```

### 노트북에서 실행하는 경우
```python
# upbit_bot/strategies/ai_market_analyzer.py
OLLAMA_BASE_URL = "http://localhost:11434"  # 로컬 노트북
OLLAMA_MODEL = "llama3.1:8b"  # 추천 모델
```

### 환경 변수로 설정 (권장)
```bash
# .env 파일 또는 환경 변수
export OLLAMA_BASE_URL="http://localhost:11434"
export OLLAMA_MODEL="llama3.1:8b"
```

코드에서 환경 변수 읽기:
```python
import os

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
```

## 7. 빠른 시작 명령어

```bash
# 1. Ollama 설치 (Linux)
curl -fsSL https://ollama.com/install.sh | sh

# 2. Ollama 서비스 시작
ollama serve &

# 3. 추천 모델 설치
ollama pull llama3.1:8b
ollama pull deepseek-r1:7b
ollama pull qwen2.5:7b

# 4. 설치 확인
ollama list

# 5. 테스트
ollama run llama3.1:8b "안녕하세요"
```

## 8. 트러블슈팅

### Ollama 서비스가 시작되지 않는 경우
```bash
# 로그 확인
journalctl -u ollama -f

# 또는 직접 실행하여 오류 확인
ollama serve
```

### 모델 다운로드가 느린 경우
```bash
# 네트워크 상태 확인
curl -I https://ollama.com

# 프록시 설정 (필요시)
export http_proxy=http://proxy.example.com:8080
export https_proxy=http://proxy.example.com:8080
```

### 메모리 부족 오류
```bash
# 사용 가능한 메모리 확인
free -h

# 더 작은 모델 사용
ollama pull llama3.1:3b  # 8B 대신 3B 사용
```

### 포트가 이미 사용 중인 경우
```bash
# 포트 사용 확인
sudo lsof -i :11434

# 다른 포트 사용
export OLLAMA_HOST=0.0.0.0:11435
```

## 9. 모델 성능 비교 및 선택

| 모델 | 크기 | RAM 필요 | 속도 | 코인 거래 적합도 | 추천도 |
|------|------|----------|------|------------------|--------|
| llama3.1:8b | 8B | ~8GB | 빠름 | ⭐⭐⭐⭐⭐ | 🏆 최고 |
| deepseek-r1:7b | 7B | ~8GB | 중간 | ⭐⭐⭐⭐⭐ | 🥈 추천 |
| qwen2.5:7b | 7B | ~8GB | 빠름 | ⭐⭐⭐⭐ | 🥉 좋음 |
| mistral:7b-instruct | 7B | ~8GB | 빠름 | ⭐⭐⭐⭐ | 좋음 |
| llama3.1:70b | 70B | ~40GB | 느림 | ⭐⭐⭐⭐⭐ | 비추천(너무 느림) |

## 10. 자동 시작 설정 (부팅 시 자동 실행)

### Systemd 서비스 (Linux)
```bash
# 서비스 파일 생성
sudo nano /etc/systemd/system/ollama.service
```

다음 내용 추가:
```ini
[Unit]
Description=Ollama Service
After=network.target

[Service]
Type=simple
User=YOUR_USERNAME
ExecStart=/usr/local/bin/ollama serve
Restart=always
Environment="OLLAMA_HOST=0.0.0.0:11434"

[Install]
WantedBy=multi-user.target
```

```bash
# 서비스 활성화
sudo systemctl daemon-reload
sudo systemctl enable ollama
sudo systemctl start ollama
```

## 11. 모델 변경 방법

코드에서 모델을 변경하려면:

```python
# upbit_bot/strategies/ai_market_analyzer.py 수정
OLLAMA_MODEL = "llama3.1:8b"  # 원하는 모델로 변경
```

또는 환경 변수로:
```bash
export OLLAMA_MODEL="llama3.1:8b"
```

## 완료!

노트북에 Ollama가 설치되고 코인 거래에 최적화된 모델들이 준비되었습니다.

다음 단계:
1. 코드에서 `OLLAMA_BASE_URL`을 `http://localhost:11434`로 변경
2. 원하는 모델로 `OLLAMA_MODEL` 변경
3. 거래 봇 실행 및 테스트

