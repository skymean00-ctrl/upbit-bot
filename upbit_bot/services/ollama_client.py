"""Ollama API 클라이언트 및 공통 유틸리티."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)

# Ollama 설정
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://100.98.189.30:11434")
OLLAMA_SCANNER_MODEL = os.getenv("OLLAMA_SCANNER_MODEL", "qwen2.5:1.5b")
OLLAMA_DECISION_MODEL = os.getenv("OLLAMA_DECISION_MODEL", "qwen2.5-coder:7b")


class OllamaError(Exception):
    """Ollama 관련 오류."""

    pass


class OllamaClient:
    """Ollama API 클라이언트 (표준화된 에러 처리)."""

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str | None = None,
        timeout: int = 45,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def verify_connection(self) -> bool:
        """Ollama 서버 연결 확인."""
        try:
            response = requests.get(f"{self.base_url}/api/tags", timeout=5)
            if response.status_code == 200:
                models = response.json().get("models", [])
                model_names = [m.get("name", "") for m in models]
                if self.model and self.model not in model_names:
                    LOGGER.warning(f"모델 '{self.model}'이 설치되어 있지 않습니다.")
                    LOGGER.info(f"사용 가능한 모델: {', '.join(model_names[:5])}")
                    return False
                return True
            return False
        except requests.exceptions.RequestException as e:
            LOGGER.error(f"Ollama 연결 실패: {e}")
            return False

    def generate(
        self,
        prompt: str,
        model: str | None = None,
        temperature: float = 0.3,
        stream: bool = False,
    ) -> str:
        """
        Ollama에 프롬프트 전송 및 응답 수신.

        Args:
            prompt: 프롬프트
            model: 사용할 모델 (None이면 self.model 사용)
            temperature: 온도 (0.0~1.0)
            stream: 스트리밍 여부

        Returns:
            응답 텍스트

        Raises:
            OllamaError: 오류 발생 시
        """
        model = model or self.model
        if not model:
            raise OllamaError("모델이 지정되지 않았습니다.")

        try:
            LOGGER.debug(f"Ollama 요청 시작: {self.base_url}/api/generate (모델: {model})")
            response = requests.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "stream": stream,
                    "temperature": temperature,
                },
                timeout=self.timeout,
            )

            if response.status_code != 200:
                error_msg = f"Ollama API 오류: HTTP {response.status_code}"
                LOGGER.error(error_msg)
                LOGGER.error(f"응답 내용: {response.text[:200]}")
                raise OllamaError(error_msg)

            result = response.json()
            response_text = result.get("response", "")

            if not response_text:
                LOGGER.warning("Ollama 응답이 비어있음")
                raise OllamaError("Ollama 응답이 비어있습니다.")

            LOGGER.debug(f"Ollama 응답 수신: {len(response_text)} 문자")
            return response_text

        except requests.exceptions.Timeout:
            error_msg = f"Ollama 요청 시간 초과 ({self.timeout}초)"
            LOGGER.error(error_msg)
            raise OllamaError(error_msg)
        except requests.exceptions.ConnectionError as e:
            error_msg = f"Ollama 연결 실패: {e}"
            LOGGER.error(error_msg)
            raise OllamaError(error_msg)
        except requests.exceptions.RequestException as e:
            error_msg = f"Ollama 요청 실패: {type(e).__name__}: {e}"
            LOGGER.error(error_msg)
            raise OllamaError(error_msg)
        except OllamaError:
            raise
        except Exception as e:
            error_msg = f"Ollama 처리 중 예기치 않은 오류: {type(e).__name__}: {e}"
            LOGGER.error(error_msg, exc_info=True)
            raise OllamaError(error_msg) from e

    def parse_json_response(self, response_text: str) -> dict[str, Any]:
        """
        Ollama 응답에서 JSON 파싱.

        Args:
            response_text: Ollama 응답 텍스트

        Returns:
            파싱된 JSON 딕셔너리

        Raises:
            OllamaError: 파싱 실패 시
        """
        try:
            # JSON 부분 추출
            json_start = response_text.find("{")
            json_end = response_text.rfind("}") + 1

            if json_start < 0 or json_end <= json_start:
                raise ValueError("JSON 형식이 응답에서 발견되지 않음")

            json_str = response_text[json_start:json_end]
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

