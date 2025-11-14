#!/bin/bash
# Windows 노트북 Ollama 연결 테스트 스크립트

echo "=========================================="
echo "Windows 노트북 Ollama 연결 테스트"
echo "=========================================="
echo ""

# 사용자에게 Windows 노트북 IP 입력 요청
if [ -z "$1" ]; then
    echo "사용법: $0 <WINDOWS_NOTEBOOK_IP>"
    echo ""
    echo "예시: $0 192.168.1.100"
    echo ""
    echo "Windows 노트북에서 IP 주소 확인 방법:"
    echo "  PowerShell: ipconfig"
    echo "  또는: Get-NetIPAddress | Where-Object {$_.InterfaceAlias -notlike '*Loopback*'}"
    exit 1
fi

WINDOWS_IP=$1
PORT=11434

echo "테스트 대상: http://${WINDOWS_IP}:${PORT}"
echo ""

# 1. Ping 테스트
echo "1️⃣  네트워크 연결 테스트 (ping)..."
if ping -c 2 -W 2 ${WINDOWS_IP} > /dev/null 2>&1; then
    echo "✅ Ping 성공"
else
    echo "❌ Ping 실패 - 네트워크 연결 확인 필요"
    exit 1
fi
echo ""

# 2. 포트 연결 테스트
echo "2️⃣  Ollama 포트 연결 테스트..."
if timeout 3 bash -c "echo > /dev/tcp/${WINDOWS_IP}/${PORT}" 2>/dev/null; then
    echo "✅ 포트 ${PORT} 연결 성공"
else
    echo "❌ 포트 ${PORT} 연결 실패"
    echo "   Windows 방화벽과 Ollama 설정을 확인하세요"
    exit 1
fi
echo ""

# 3. Ollama API 테스트
echo "3️⃣  Ollama API 연결 테스트..."
response=$(curl -s -w "\n%{http_code}" --connect-timeout 5 "http://${WINDOWS_IP}:${PORT}/api/tags" 2>/dev/null)
http_code=$(echo "$response" | tail -1)
body=$(echo "$response" | head -n -1)

if [ "$http_code" = "200" ]; then
    echo "✅ Ollama API 연결 성공"
    echo ""
    echo "📦 설치된 모델:"
    echo "$body" | python3 -c "import sys, json; models = json.load(sys.stdin).get('models', []); [print(f'  - {m.get(\"name\", \"unknown\")}') for m in models]" 2>/dev/null || echo "  (모델 목록 파싱 실패)"
else
    echo "❌ Ollama API 연결 실패 (HTTP $http_code)"
    echo "   응답: $body"
    exit 1
fi
echo ""

# 4. 모델 테스트 (llama3.1:8b)
echo "4️⃣  모델 'llama3.1:8b' 테스트..."
model_response=$(curl -s -w "\n%{http_code}" --connect-timeout 10 \
    -X POST \
    -H "Content-Type: application/json" \
    -d '{"model":"llama3.1:8b","prompt":"테스트","stream":false}' \
    "http://${WINDOWS_IP}:${PORT}/api/generate" 2>/dev/null)
model_http_code=$(echo "$model_response" | tail -1)

if [ "$model_http_code" = "200" ]; then
    echo "✅ 모델 'llama3.1:8b' 응답 성공"
elif [ "$model_http_code" = "404" ]; then
    echo "⚠️  모델 'llama3.1:8b'가 설치되지 않았습니다"
    echo "   Windows 노트북에서 실행: ollama pull llama3.1:8b"
else
    echo "⚠️  모델 테스트 실패 (HTTP $model_http_code)"
fi
echo ""

echo "=========================================="
echo "✅ 테스트 완료!"
echo "=========================================="
echo ""
echo "코드 설정:"
echo "  OLLAMA_BASE_URL = \"http://${WINDOWS_IP}:${PORT}\""
echo "  OLLAMA_MODEL = \"llama3.1:8b\""
echo ""
