"""Ollama API 클라이언트 및 공통 유틸리티."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Optional

import requests

LOGGER = logging.getLogger(__name__)

# Ollama 설정 (서버 단일 환경 기준)
# 기본적으로 로컬 서버에서 각 업무별로 최적화된 경량 모델을 사용합니다.
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")

# 업무별 모델 설정 (서버 부하 최소화를 위해 경량 모델 사용)
# 1. 스캐너 모델: 정보 수집용 (가장 경량, 빠른 스캔)
OLLAMA_SCANNER_MODEL = os.getenv("OLLAMA_SCANNER_MODEL", "qwen2.5:0.5b")

# 2. 1차 분석 모델: 거래량 상위 30개 스캔 (경량, 빠른 처리)
OLLAMA_FIRST_ROUND_MODEL = os.getenv("OLLAMA_FIRST_ROUND_MODEL", "qwen2.5:0.5b")

# 3. 2차 분석 모델: 상위 10개 선정 (중간 경량, 점수 계산)
# 1.5b 모델 사용 (최종 결정과 같은 모델 재사용으로 메모리 효율적)
OLLAMA_SECOND_ROUND_MODEL = os.getenv("OLLAMA_SECOND_ROUND_MODEL", "qwen2.5:1.5b")

# 4. 최종 결정 모델: 상위 5개 중 최종 선택 (약간 더 큰 모델, 판단력 필요)
OLLAMA_DECISION_MODEL = os.getenv("OLLAMA_DECISION_MODEL", "qwen2.5:1.5b")

# 호환성을 위한 레거시 변수 (기본값을 결정자 모델로 설정)
OLLAMA_LEGACY_MODEL = os.getenv("OLLAMA_LEGACY_MODEL", OLLAMA_DECISION_MODEL)


class OllamaError(Exception):
    """Ollama 관련 오류."""

    pass


class OllamaClient:
    """Ollama API 클라이언트 (표준화된 에러 처리)."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int = 120,  # 타임아웃 120초로 증가 (Ollama 응답 지연 대응)
    ) -> None:
        # base_url이 None이면 기본값 사용
        url = base_url or OLLAMA_BASE_URL
        self.base_url = url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def verify_connection(self, quick_check: bool = False) -> bool:
        """
        Ollama 서버 연결 및 모델 설치 확인.
        
        Args:
            quick_check: True면 빠른 확인만 (3초 타임아웃, 모델 확인 생략)
        
        Returns:
            연결 성공 여부
        """
        check_timeout = 3 if quick_check else 5
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=check_timeout)
            if response.status_code == 200:
                if quick_check:
                    return True  # 빠른 확인은 여기서 종료
                
                models = response.json().get("models", [])
                model_names = [m.get("name", "") for m in models]
                
                if not model_names:
                    LOGGER.warning("Ollama 서버에 설치된 모델이 없습니다.")
                    return False
                
                # 모델 확인 (정확한 이름 또는 부분 매칭)
                if self.model:
                    model_found = (
                        self.model in model_names
                        or any(
                            self.model.replace(":", "") in name.replace(":", "")
                            for name in model_names
                        )
                        or any(
                            all(part in name for part in self.model.split(":"))
                            for name in model_names
                        )
                    )
                    
                    if not model_found:
                        LOGGER.warning(
                            f"모델 '{self.model}'이 설치되어 있지 않습니다.\n"
                            f"사용 가능한 모델: {', '.join(model_names[:5])}"
                        )
                        LOGGER.info(
                            f"설치 방법: python scripts/install_ollama_model.py {self.model}"
                        )
                        return False
                    
                    LOGGER.debug(f"모델 '{self.model}' 확인됨")
                
                return True
            return False
        except requests.exceptions.ConnectTimeout:
            LOGGER.warning(f"Ollama 서버 연결 시간 초과 ({check_timeout}초): {self.base_url}")
            if not quick_check:
                LOGGER.info("서버 Ollama 서버가 실행 중인지 확인하세요: 'ollama serve' 또는 'systemctl status ollama'")
            return False
        except requests.exceptions.ConnectionError:
            LOGGER.warning(f"Ollama 서버에 연결할 수 없습니다: {self.base_url}")
            if not quick_check:
                LOGGER.info("서버 Ollama 서버가 실행 중이지 않습니다. 'ollama serve' 또는 'systemctl start ollama'를 실행하세요.")
            return False
        except requests.exceptions.RequestException as e:
            LOGGER.error(f"Ollama 연결 실패: {e}")
            return False

    def generate(
        self,
        prompt: str,
        model: str | None = None,
        temperature: float = 0.3,
        max_retries: int = 2,
    ) -> str:
        """
        Ollama에 프롬프트 전송 및 응답 수신 (비스트리밍 모드, 재시도 로직 포함).

        Args:
            prompt: 프롬프트
            model: 사용할 모델 (None이면 self.model 사용)
            temperature: 온도 (0.0~1.0)
            max_retries: 최대 재시도 횟수 (기본값: 2)

        Returns:
            응답 텍스트

        Raises:
            OllamaError: 오류 발생 시
        """
        model = model or self.model
        if not model:
            raise OllamaError("모델이 지정되지 않았습니다.")

        last_error: Optional[Exception] = None
        
        # 단계별 타임아웃: 연결 확인(3초) → 요청(30초) → 전체(120초)
        CONNECTION_CHECK_TIMEOUT = 3
        FIRST_ATTEMPT_TIMEOUT = 30
        FULL_TIMEOUT = self.timeout
        
        for attempt in range(max_retries):
            try:
                # 1단계: 빠른 연결 확인 (첫 시도만, 3초 타임아웃)
                if attempt == 0:
                    if not self.verify_connection(quick_check=True):
                        if max_retries > 1:
                            LOGGER.warning(f"연결 확인 실패 (시도 {attempt + 1}/{max_retries}), 재시도")
                            time.sleep(1 * (attempt + 1))  # Exponential backoff
                            continue
                        else:
                            raise OllamaError(f"Ollama 서버 연결 실패: {self.base_url}")
                
                # 2단계: 실제 요청 (단계별 타임아웃)
                LOGGER.debug(
                    f"Ollama 요청 시작 (시도 {attempt + 1}/{max_retries}): "
                    f"{self.base_url}/api/generate (모델: {model})"
                )
                
                # 첫 시도: 30초 타임아웃, 재시도: 전체 타임아웃
                request_timeout = FIRST_ATTEMPT_TIMEOUT if attempt == 0 else FULL_TIMEOUT
                
                response = requests.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": False,  # 비스트리밍 모드 (최소 버전 호환)
                        "temperature": temperature,
                    },
                    timeout=request_timeout,
                )

                if response.status_code != 200:
                    error_data = response.json() if response.content else {}
                    error_msg = error_data.get("error", {}).get("message", f"HTTP {response.status_code}")
                    raise OllamaError(f"Ollama API 오류: {error_msg}")

                result = response.json()
                response_text = result.get("response", "")

                if not response_text:
                    raise OllamaError("Ollama 응답이 비어있습니다.")

                LOGGER.debug(f"Ollama 응답 수신: {len(response_text)} 문자")
                return response_text

            except requests.exceptions.Timeout as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = 2 * (attempt + 1)  # Exponential backoff: 2초, 4초
                    LOGGER.warning(
                        f"타임아웃 발생 (시도 {attempt + 1}/{max_retries}, 타임아웃: {request_timeout}초), "
                        f"{wait_time}초 후 재시도"
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    error_msg = f"Ollama 요청 시간 초과 ({FULL_TIMEOUT}초, {max_retries}회 시도)"
                    LOGGER.error(error_msg)
                    raise OllamaError(error_msg)
                    
            except requests.exceptions.ConnectionError as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = 1 * (attempt + 1)  # Exponential backoff: 1초, 2초
                    LOGGER.warning(
                        f"연결 실패 (시도 {attempt + 1}/{max_retries}), {wait_time}초 후 재시도: {e}"
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    error_msg = f"Ollama 연결 실패 ({max_retries}회 시도): {e}"
                    LOGGER.error(error_msg)
                    raise OllamaError(error_msg)
                    
            except OllamaError:
                raise  # OllamaError는 재시도하지 않음
                
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = 1 * (attempt + 1)
                    LOGGER.warning(
                        f"예기치 않은 오류 (시도 {attempt + 1}/{max_retries}): {e}, {wait_time}초 후 재시도"
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    error_msg = f"Ollama 요청 실패: {type(e).__name__}: {e}"
                    LOGGER.error(error_msg, exc_info=True)
                    raise OllamaError(error_msg) from e
        
        # 모든 재시도 실패
        raise OllamaError(f"Ollama 요청 최종 실패 ({max_retries}회 시도): {last_error}")

    def parse_json_response(self, response_text: str) -> dict[str, Any]:
        """
        Ollama 응답에서 JSON 파싱.
        마크다운 코드 블록(```json ... ```)을 처리하고 JSON만 추출.

        Args:
            response_text: Ollama 응답 텍스트

        Returns:
            파싱된 JSON 딕셔너리

        Raises:
            OllamaError: 파싱 실패 시
        """
        try:
            # 1. 마크다운 코드 블록 제거 (```json ... ```)
            # ```json 또는 ``` 로 시작하는 코드 블록 제거
            cleaned = re.sub(r'```json\s*', '', response_text, flags=re.IGNORECASE)
            cleaned = re.sub(r'```\s*$', '', cleaned, flags=re.MULTILINE)
            cleaned = cleaned.strip()
            
            # 2. JSON 부분 추출 (첫 번째 { 부터 마지막 } 까지)
            json_start = cleaned.find("{")
            json_end = cleaned.rfind("}") + 1

            if json_start < 0 or json_end <= json_start:
                raise ValueError("JSON 형식이 응답에서 발견되지 않음")

            json_str = cleaned[json_start:json_end]
            
            # 3. JSON 파싱
            data = json.loads(json_str)
            return data

        except json.JSONDecodeError as e:
            error_msg = f"JSON 파싱 실패: {e}"
            LOGGER.warning(f"{error_msg}\n응답 텍스트: {response_text[:200]}")
            raise OllamaError(error_msg) from e
        except Exception as e:
            error_msg = f"응답 파싱 중 오류: {type(e).__name__}: {e}"
            LOGGER.error(error_msg)
            raise OllamaError(error_msg) from e

