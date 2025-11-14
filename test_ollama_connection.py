#!/usr/bin/env python3
"""Ollama 연결 테스트 스크립트"""

import requests
import sys

OLLAMA_BASE_URL = "http://100.98.189.30:11434"
OLLAMA_MODEL = "qwen2.5-coder:7b"

def test_ollama_connection():
    """Ollama 서버 연결 테스트"""
    print("=" * 60)
    print("Ollama 서버 연결 테스트")
    print("=" * 60)
    print(f"서버 주소: {OLLAMA_BASE_URL}")
    print(f"사용 모델: {OLLAMA_MODEL}")
    print()
    
    # 1. /api/tags 테스트
    print("1. /api/tags 엔드포인트 테스트...")
    try:
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json().get("models", [])
            print(f"   ✅ 연결 성공! 사용 가능한 모델: {len(models)}개")
            for model in models[:5]:  # 처음 5개만 표시
                model_name = model.get("name", "Unknown")
                print(f"      - {model_name}")
            if len(models) > 5:
                print(f"      ... 외 {len(models) - 5}개")
            
            # 필요한 모델이 있는지 확인
            model_names = [m.get("name", "") for m in models]
            if OLLAMA_MODEL in model_names:
                print(f"   ✅ 필요한 모델 '{OLLAMA_MODEL}' 발견!")
            else:
                print(f"   ⚠️  필요한 모델 '{OLLAMA_MODEL}' 없음")
                print(f"   사용 가능한 모델 목록:")
                for model_name in model_names:
                    print(f"      - {model_name}")
        else:
            print(f"   ❌ 연결 실패: HTTP {response.status_code}")
            print(f"   응답: {response.text[:200]}")
            return False
    except requests.exceptions.Timeout:
        print(f"   ❌ 연결 시간 초과 (5초)")
        print(f"   서버가 실행 중인지 확인하세요: {OLLAMA_BASE_URL}")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"   ❌ 연결 오류: {e}")
        print(f"   서버가 실행 중인지 확인하세요: {OLLAMA_BASE_URL}")
        return False
    except Exception as e:
        print(f"   ❌ 예기치 않은 오류: {e}")
        return False
    
    print()
    
    # 2. /api/generate 테스트 (간단한 요청)
    print("2. /api/generate 엔드포인트 테스트...")
    try:
        test_prompt = "테스트: 1+1은?"
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": test_prompt,
                "stream": False,
                "options": {
                    "num_predict": 10  # 짧게만 생성
                }
            },
            timeout=30
        )
        if response.status_code == 200:
            result = response.json()
            response_text = result.get("response", "")
            print(f"   ✅ 생성 성공!")
            print(f"   테스트 응답: {response_text[:100]}")
            return True
        else:
            print(f"   ❌ 생성 실패: HTTP {response.status_code}")
            print(f"   응답: {response.text[:200]}")
            return False
    except requests.exceptions.Timeout:
        print(f"   ⚠️  생성 시간 초과 (30초)")
        print(f"   모델이 너무 느리거나 서버 응답이 느릴 수 있습니다.")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"   ❌ 연결 오류: {e}")
        return False
    except Exception as e:
        print(f"   ❌ 예기치 않은 오류: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_ollama_connection()
    print()
    print("=" * 60)
    if success:
        print("✅ Ollama 서버 연결 테스트 통과!")
        sys.exit(0)
    else:
        print("❌ Ollama 서버 연결 테스트 실패!")
        print()
        print("해결 방법:")
        print(f"1. 노트북에서 Ollama 서버 실행: ollama serve")
        print(f"2. 서버 주소 확인: {OLLAMA_BASE_URL}")
        print(f"3. 방화벽 설정 확인")
        sys.exit(1)

