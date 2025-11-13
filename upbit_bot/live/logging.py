"""Logging utilities for the live trading process."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

DEFAULT_LOG_DIR = Path("logs")


@dataclass
class TradeLogger:
    """Structured logging wrapper for executions, errors, and pnl."""

    log_dir: Path = DEFAULT_LOG_DIR
    logger_name: str = "upbit_bot.live"
    level: int = logging.INFO
    _logger: logging.Logger = field(init=False)

    def __post_init__(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._logger = logging.getLogger(self.logger_name)
        self._logger.setLevel(self.level)
        if not self._logger.handlers:
            handler = logging.FileHandler(self.log_dir / "trading.log")
            formatter = logging.Formatter(
                "%(asctime)s | %(levelname)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
            handler.setFormatter(formatter)
            self._logger.addHandler(handler)

    def _log(self, event_type: str, payload: Dict[str, Any]) -> None:
        entry = {"type": event_type, **payload}
        self._logger.info(json.dumps(entry, ensure_ascii=False, default=str))

    def log_execution(
        self,
        order_id: str,
        side: str,
        price: float,
        quantity: float,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        payload = {
            "timestamp": datetime.utcnow().isoformat(),
            "order_id": order_id,
            "side": side,
            "price": price,
            "quantity": quantity,
        }
        if extra:
            payload.update(extra)
        self._log("execution", payload)

    def log_error(self, message: str, *, exception: Optional[Exception] = None) -> None:
        payload = {"timestamp": datetime.utcnow().isoformat(), "message": message}
        if exception:
            payload["exception"] = repr(exception)
        self._log("error", payload)

    def log_pnl(
        self,
        realized: float,
        unrealized: float,
        *,
        positions: Optional[Iterable[Dict[str, Any]]] = None,
    ) -> None:
        payload = {
            "timestamp": datetime.utcnow().isoformat(),
            "realized": realized,
            "unrealized": unrealized,
        }
        if positions is not None:
            payload["positions"] = list(positions)
        self._log("pnl", payload)


__all__ = ["TradeLogger"]
