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
