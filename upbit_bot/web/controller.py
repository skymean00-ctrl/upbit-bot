"""Controller utilities for managing the execution engine from the web UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from threading import Lock
from typing import Any

from upbit_bot.core import UpbitClient
from upbit_bot.services.execution import ExecutionEngine
from upbit_bot.strategies import StrategySignal


@dataclass
class TradingState:
    running: bool
    dry_run: bool
    market: str
    strategy: str
    min_order_amount: float
    last_signal: str | None
    last_run_at: str | None
    last_error: str | None
    last_order: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class TradingController:
    """Wraps the execution engine with start/stop helpers and convenience status calls."""

    def __init__(self, engine: ExecutionEngine, client: UpbitClient) -> None:
        self.engine = engine
        self.client = client
        self._lock = Lock()

    def start(self) -> None:
        with self._lock:
            self.engine.start_async()

    def stop(self) -> None:
        with self._lock:
            self.engine.stop()

    def get_state(self) -> TradingState:
        last_signal = self.engine.last_signal.name if self.engine.last_signal else None
        if last_signal and last_signal not in StrategySignal.__members__:
            last_signal = str(last_signal)
        last_run_dt = getattr(self.engine, "last_run_at", None)
        last_run_at = last_run_dt.isoformat() if isinstance(last_run_dt, datetime) else None
        state = TradingState(
            running=self.engine.is_running(),
            dry_run=self.engine.dry_run,
            market=self.engine.market,
            strategy=self.engine.strategy.name,
            min_order_amount=self.engine.min_order_amount,
            last_signal=last_signal,
            last_run_at=last_run_at,
            last_error=self.engine.last_error,
            last_order=self.engine.last_order_info,
        )
        return state
    
    def get_ai_analysis(self) -> dict[str, Any] | None:
        """AI 분석 결과 가져오기."""
        return getattr(self.engine, 'last_ai_analysis', None)
    
    def get_ollama_status(self) -> dict[str, Any]:
        """Ollama 연결 상태 확인 (스캐너 모델과 결정자 모델 모두 확인)."""
        import os
        import requests
        import logging
        
        LOGGER = logging.getLogger(__name__)
        
        # 서버 로컬 Ollama 사용 (환경 변수 또는 기본값)
        from upbit_bot.services.ollama_client import OLLAMA_BASE_URL
        ollama_base_url = os.getenv("OLLAMA_SCANNER_URL") or os.getenv("OLLAMA_BASE_URL") or OLLAMA_BASE_URL
        # 단일 모델 구조 (현재는 1.5b 동일 모델 사용)
        scanner_model = os.getenv("OLLAMA_SCANNER_MODEL", "qwen2.5:1.5b")
        decision_model = os.getenv("OLLAMA_DECISION_MODEL", "qwen2.5:1.5b")
        
        status = {
            "connected": False,
            "url": ollama_base_url,
            "scanner_model": scanner_model,
            "decision_model": decision_model,
            "scanner_model_available": False,
            "decision_model_available": False,
            "model_available": False,  # 두 모델 모두 사용 가능한지
            "available_models": [],
            "error": None,
        }
        
        try:
            LOGGER.debug(f"Ollama 연결 확인 시작: {ollama_base_url}")
            # 연결 확인 (5초 타임아웃)
            response = requests.get(
                f"{ollama_base_url}/api/tags",
                timeout=5,
            )
            
            if response.status_code == 200:
                data = response.json()
                models = data.get("models", [])
                model_names = [m.get("name", "") for m in models]
                
                status["connected"] = True
                status["available_models"] = model_names
                
                # 스캐너 모델 확인 (부분 매칭 포함)
                scanner_found = False
                for name in model_names:
                    # 정확한 이름 또는 부분 매칭
                    if (scanner_model in name or 
                        name in scanner_model or
                        scanner_model.replace(":", "") in name.replace(":", "") or
                        all(part in name for part in scanner_model.split(":") if part)):
                        scanner_found = True
                        status["scanner_model_available"] = True
                        status["model"] = name  # 실제 설치된 모델 이름
                        break
                
                # 결정자 모델 확인 (부분 매칭 포함)
                decision_found = False
                for name in model_names:
                    if (decision_model in name or 
                        name in decision_model or
                        decision_model.replace(":", "") in name.replace(":", "") or
                        all(part in name for part in decision_model.split(":") if part)):
                        decision_found = True
                        status["decision_model_available"] = True
                        if "model" not in status:
                            status["model"] = name
                        break
                
                # 두 모델 모두 사용 가능한지 확인 (현재는 동일 모델이므로 하나만 있어도 OK)
                status["model_available"] = scanner_found and decision_found
                
                if not scanner_found:
                    LOGGER.warning(f"스캐너 모델 '{scanner_model}'을 찾을 수 없습니다. 사용 가능한 모델: {', '.join(model_names[:5])}")
                if not decision_found:
                    LOGGER.warning(f"결정자 모델 '{decision_model}'을 찾을 수 없습니다. 사용 가능한 모델: {', '.join(model_names[:5])}")
                    
            else:
                error_msg = f"HTTP {response.status_code}: {response.text[:100]}"
                status["error"] = error_msg
                LOGGER.error(f"Ollama API 오류: {error_msg}")
                
        except requests.exceptions.Timeout:
            error_msg = "연결 시간 초과 (5초)"
            status["error"] = error_msg
            LOGGER.warning(f"Ollama 서버 연결 시간 초과: {ollama_base_url}")
            LOGGER.info("서버 Ollama 서버가 실행 중인지 확인하세요: 'ollama serve' 또는 'systemctl status ollama'")
        except requests.exceptions.ConnectionError as e:
            error_msg = f"연결 실패: {str(e)[:100]}"
            status["error"] = error_msg
            LOGGER.warning(f"Ollama 서버에 연결할 수 없습니다: {ollama_base_url}")
            LOGGER.info("서버 Ollama 서버가 실행 중이지 않습니다. 'ollama serve' 또는 'systemctl start ollama'를 실행하세요.")
        except Exception as e:
            error_msg = f"오류: {str(e)[:100]}"
            status["error"] = error_msg
            LOGGER.error(f"Ollama 상태 확인 중 예기치 않은 오류: {e}", exc_info=True)
        
        LOGGER.debug(f"Ollama 상태 확인 완료: connected={status['connected']}, scanner={status['scanner_model_available']}, decision={status['decision_model_available']}")
        return status

    def get_account_overview(self) -> dict[str, Any]:
        """
        업비트 계정 요약 정보 조회.

        - 인증 오류(401 / invalid_access_key)와
          네트워크/타임아웃 오류를 구분해서 UI에 전달한다.
        """
        try:
            accounts = self.client.get_accounts()
        except Exception as exc:  # noqa: BLE001
            # 에러 메시지를 그대로 넘기되, 이후 템플릿에서 유형별로 분기
            return {"error": str(exc), "accounts": []}

        overview: dict[str, Any] = {"accounts": accounts}
        try:
            krw_balance = next(
                (
                    float(account.get("balance", 0.0))
                    for account in accounts
                    if account.get("currency") == "KRW"
                ),
                0.0,
            )
            overview["krw_balance"] = krw_balance
        except (TypeError, ValueError):
            overview["krw_balance"] = 0.0
        return overview
