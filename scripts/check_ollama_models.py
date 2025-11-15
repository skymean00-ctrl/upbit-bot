#!/usr/bin/env python3
"""
Ollama 모델 설치 확인 스크립트

사용법:
    python scripts/check_ollama_models.py
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import requests
import os

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://100.98.189.30:11434")
REQUIRED_MODELS = ["qwen2.5:1.5b", "qwen2.5-coder:7b"]


def check_models():
    """설치된 Ollama 모델 확인."""
    print("=" * 60)
    print("Ollama 모델 설치 확인")
    print("=" * 60)
    print(f"서버: {OLLAMA_BASE_URL}\n")

    try:
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if response.status_code != 200:
            print(f"❌ Ollama 서버 응답 오류: HTTP {response.status_code}")
            return False

        models = response.json().get("models", [])
        model_names = [m.get("name", "") for m in models]

        print("📦 설치된 모델:")
        if model_names:
            for name in sorted(model_names):
                print(f"  ✅ {name}")
        else:
            print("  (설치된 모델 없음)")

        print(f"\n🔍 필요한 모델 확인:")
        all_installed = True
        for required in REQUIRED_MODELS:
            # 정확한 이름 매칭 또는 부분 매칭
            is_installed = (
                required in model_names
                or any(required.replace(":", "") in name.replace(":", "") for name in model_names)
                or any(all(part in name for part in required.split(":")) for name in model_names)
            )

            status = "✅ 설치됨" if is_installed else "❌ 미설치"
            print(f"  {status}: {required}")

            if not is_installed:
                all_installed = False

        print("\n" + "=" * 60)
        if all_installed:
            print("✅ 모든 필요한 모델이 설치되어 있습니다!")
            return True
        else:
            print("⚠️  일부 모델이 설치되어 있지 않습니다.")
            print("\n설치 방법:")
            print(f"  python scripts/install_ollama_model.py <모델명>")
            return False

    except requests.exceptions.ConnectTimeout:
        print(f"❌ Ollama 서버 연결 시간 초과")
        print(f"노트북({OLLAMA_BASE_URL})이 켜져 있고 Ollama 서버가 실행 중인지 확인하세요.")
        return False
    except requests.exceptions.ConnectionError:
        print(f"❌ Ollama 서버에 연결할 수 없습니다")
        print(f"노트북({OLLAMA_BASE_URL})이 켜져 있고 Ollama 서버가 실행 중인지 확인하세요.")
        print("\n노트북 전원 켜기:")
        print("  python scripts/wake_laptop.py")
        return False
    except Exception as e:
        print(f"❌ 오류 발생: {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    success = check_models()
    sys.exit(0 if success else 1)

