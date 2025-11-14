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
        """Ollama 연결 상태 확인."""
        import os
        import requests
        
        ollama_base_url = os.getenv("OLLAMA_BASE_URL", "http://100.98.189.30:11434")
        ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")
        
        status = {
            "connected": False,
            "url": ollama_base_url,
            "model": ollama_model,
            "available_models": [],
            "error": None,
        }
        
        try:
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
                status["model_available"] = any(
                    ollama_model in name or name in ollama_model
                    for name in model_names
                )
            else:
                status["error"] = f"HTTP {response.status_code}: {response.text[:100]}"
                
        except requests.exceptions.Timeout:
            status["error"] = "연결 시간 초과 (5초)"
        except requests.exceptions.ConnectionError as e:
            status["error"] = f"연결 실패: {str(e)[:100]}"
        except Exception as e:
            status["error"] = f"오류: {str(e)[:100]}"
        
        return status

    def get_account_overview(self) -> dict[str, Any]:
        try:
            accounts = self.client.get_accounts()
        except Exception as exc:  # noqa: BLE001
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
