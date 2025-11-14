#!/bin/bash
# Ollama 모델 전환 스크립트

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://100.98.189.30:11434}"

echo "=========================================="
echo "Ollama 모델 전환"
echo "=========================================="
echo ""
echo "현재 Ollama 서버: $OLLAMA_BASE_URL"
echo ""

if [ -z "$1" ]; then
    echo "사용법: $0 <모델명>"
    echo ""
    echo "사용 가능한 모델:"
    echo "  1. qwen2.5-coder:7b (기본)"
    echo "  2. llama3.1:8b (추천)"
    echo "  3. deepseek-r1:7b (수치 분석 강점)"
    echo ""
    echo "예시:"
    echo "  $0 llama3.1:8b"
    echo "  $0 deepseek-r1:7b"
    echo ""
    echo "환경 변수 설정 방법:"
    echo "  export OLLAMA_MODEL=llama3.1:8b"
    echo "  export OLLAMA_BASE_URL=http://100.98.189.30:11434"
    exit 1
fi

MODEL=$1

echo "🔍 모델 확인 중..."
response=$(curl -s "$OLLAMA_BASE_URL/api/tags" 2>/dev/null)
if [ $? -ne 0 ]; then
    echo "❌ Ollama 서버에 연결할 수 없습니다: $OLLAMA_BASE_URL"
    exit 1
fi

# 모델 목록에서 확인
if echo "$response" | python3 -c "import sys, json; models = [m['name'] for m in json.load(sys.stdin).get('models', [])]; sys.exit(0 if '$MODEL' in models else 1)" 2>/dev/null; then
    echo "✅ 모델 '$MODEL' 확인됨"
else
    echo "⚠️  모델 '$MODEL'이 서버에 설치되어 있지 않을 수 있습니다"
    echo "   Ollama 서버에서 확인: curl $OLLAMA_BASE_URL/api/tags"
fi

echo ""
echo "📝 환경 변수 설정 명령어:"
echo ""
echo "export OLLAMA_BASE_URL=\"$OLLAMA_BASE_URL\""
echo "export OLLAMA_MODEL=\"$MODEL\""
echo ""
echo "웹서버 재시작:"
echo "  ps aux | grep web_dashboard | grep -v grep | awk '{print \$2}' | xargs kill -15"
echo "  sleep 2"
echo "  cd /home/skymean00/projects/upbit-bot"
echo "  PYTHONPATH=/home/skymean00/projects/upbit-bot:\$PYTHONPATH nohup python3 scripts/web_dashboard.py --host 0.0.0.0 --port 8080 > logs/web_server.log 2>&1 &"
echo ""
echo "✅ 설정 완료! 위 명령어를 실행하세요."

