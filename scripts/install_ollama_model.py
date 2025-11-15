#!/usr/bin/env python3
"""
Ollama 원격 서버에 모델 설치 스크립트

사용법:
    python scripts/install_ollama_model.py qwen2.5:1.5b
    python scripts/install_ollama_model.py qwen2.5:1.5b --verify
"""

import sys
import requests
import json
import time
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

OLLAMA_BASE_URL = "http://100.98.189.30:11434"


def verify_model_installed(model_name: str) -> bool:
    """
    모델이 설치되어 있는지 확인
    """
    try:
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if response.status_code == 200:
            models = response.json().get("models", [])
            model_names = [m.get("name", "") for m in models]
            
            if model_name in model_names:
                print(f"✅ 모델 '{model_name}'이(가) 이미 설치되어 있습니다.")
                return True
            else:
                print(f"❌ 모델 '{model_name}'이(가) 설치되어 있지 않습니다.")
                print(f"\n설치된 모델 목록:")
                for name in model_names:
                    print(f"  - {name}")
                return False
        else:
            print(f"❌ 모델 목록 조회 실패: HTTP {response.status_code}")
            return False
    except requests.exceptions.ConnectionError:
        print(f"❌ Ollama 서버에 연결할 수 없습니다: {OLLAMA_BASE_URL}")
        print("노트북에서 'ollama serve'가 실행 중인지 확인하세요.")
        return False
    except Exception as e:
        print(f"❌ 확인 실패: {e}")
        return False


def install_ollama_model_remote(model_name: str) -> bool:
    """
    원격 Ollama 서버에 모델 설치
    
    Args:
        model_name: 설치할 모델 이름 (예: "qwen2.5:1.5b")
    
    Returns:
        설치 성공 여부
    """
    print(f"\n{'='*60}")
    print(f"모델 '{model_name}' 설치 시작")
    print(f"Ollama 서버: {OLLAMA_BASE_URL}")
    print(f"{'='*60}\n")
    
    try:
        # Ollama /api/pull 엔드포인트 호출
        response = requests.post(
            f"{OLLAMA_BASE_URL}/api/pull",
            json={"name": model_name},
            stream=True,
            timeout=600  # 10분 타임아웃
        )
        
        if response.status_code != 200:
            print(f"❌ 설치 실패: HTTP {response.status_code}")
            print(f"응답: {response.text[:200]}")
            return False
        
        print("다운로드 진행 중...\n")
        last_completed = 0
        last_total = 0
        
        for line in response.iter_lines():
            if line:
                try:
                    data = json.loads(line.decode('utf-8'))
                    status = data.get("status", "")
                    
                    # 다운로드 진행률 표시
                    if "completed" in data and "total" in data:
                        completed = data.get("completed", 0)
                        total = data.get("total", 0)
                        
                        if total > 0:
                            percent = (completed / total) * 100
                            mb_completed = completed / (1024 * 1024)
                            mb_total = total / (1024 * 1024)
                            
                            # 진행률이 변경될 때만 출력 (화면 깜빡임 방지)
                            if completed != last_completed or total != last_total:
                                print(f"\r진행률: {percent:.1f}% ({mb_completed:.1f}MB / {mb_total:.1f}MB)", 
                                      end="", flush=True)
                                last_completed = completed
                                last_total = total
                    
                    # 상태 메시지 출력
                    elif status:
                        if "pulling" in status.lower() or "verifying" in status.lower():
                            print(f"\n{status}")
                        elif "success" in status.lower() or "downloaded" in status.lower():
                            print(f"\n✅ {status}")
                        elif "error" in status.lower() or "failed" in status.lower():
                            print(f"\n❌ {status}")
                            
                except json.JSONDecodeError:
                    # JSON 파싱 실패 시 무시 (진행 메시지일 수 있음)
                    pass
                except Exception as e:
                    print(f"\n⚠️ 진행 상황 파싱 오류: {e}")
        
        print(f"\n\n{'='*60}")
        print(f"✅ 모델 '{model_name}' 설치 완료!")
        print(f"{'='*60}\n")
        return True
        
    except requests.exceptions.Timeout:
        print(f"\n❌ 타임아웃: 모델 설치에 시간이 너무 오래 걸립니다 (10분 초과)")
        print("네트워크 상태와 모델 크기를 확인하세요.")
        return False
    except requests.exceptions.ConnectionError as e:
        print(f"\n❌ 연결 실패: Ollama 서버에 연결할 수 없습니다")
        print(f"서버 주소: {OLLAMA_BASE_URL}")
        print(f"오류: {e}")
        print("\n해결 방법:")
        print("1. Ollama 서버가 실행 중인지 확인: ollama serve")
        print("2. 네트워크 연결 확인")
        print("3. 방화벽 설정 확인")
        return False
    except Exception as e:
        print(f"\n❌ 예기치 않은 오류: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """메인 함수"""
    if len(sys.argv) < 2:
        print("사용법: python scripts/install_ollama_model.py <모델명> [--verify]")
        print("\n예시:")
        print("  python scripts/install_ollama_model.py qwen2.5:1.5b")
        print("  python scripts/install_ollama_model.py qwen2.5:1.5b --verify")
        sys.exit(1)
    
    model_name = sys.argv[1]
    verify_only = "--verify" in sys.argv
    
    print(f"{'='*60}")
    print("Ollama 원격 모델 설치 도구")
    print(f"{'='*60}")
    print(f"대상 서버: {OLLAMA_BASE_URL}")
    print(f"모델: {model_name}")
    print(f"{'='*60}\n")
    
    # 1. 먼저 확인
    print("1. 현재 설치된 모델 확인 중...")
    is_installed = verify_model_installed(model_name)
    
    if verify_only:
        sys.exit(0 if is_installed else 1)
    
    if is_installed:
        print(f"\n모델 '{model_name}'이(가) 이미 설치되어 있습니다.")
        response = input("재설치하시겠습니까? (y/N): ")
        if response.lower() != 'y':
            print("설치를 취소했습니다.")
            sys.exit(0)
    
    # 2. 모델 설치
    print("\n2. 모델 설치 진행...")
    success = install_ollama_model_remote(model_name)
    
    if not success:
        print("\n❌ 모델 설치에 실패했습니다.")
        sys.exit(1)
    
    # 3. 설치 확인
    print("\n3. 설치 확인 중...")
    time.sleep(2)  # 서버 처리 시간 대기
    is_installed = verify_model_installed(model_name)
    
    if is_installed:
        print(f"\n✅ 모든 작업이 완료되었습니다!")
        print(f"모델 '{model_name}'을(를) 사용할 수 있습니다.")
        sys.exit(0)
    else:
        print(f"\n⚠️ 설치가 완료되었지만 확인에 실패했습니다.")
        print("잠시 후 다시 확인해보세요.")
        sys.exit(1)


if __name__ == "__main__":
    main()

