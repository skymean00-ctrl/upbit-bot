"""Configuration helpers for the trading bot."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from src.strategies.moving_average import MovingAverageConfig


@dataclass
class StrategySettings:
    moving_average: MovingAverageConfig = MovingAverageConfig()


@dataclass
class RiskSettings:
    max_position: float = 0.001  # BTC size
    max_notional: float = 500_000  # KRW


@dataclass
class AppSettings:
    access_key: str
    secret_key: str
    market: str = "KRW-BTC"
    candle_interval: str = "minutes/1"
    candle_count: int = 200
    order_amount: float = 10_000  # KRW
    poll_interval: float = 5.0
    strategy: StrategySettings = StrategySettings()
    risk: RiskSettings = RiskSettings()


def load_settings(path: str | None = None) -> AppSettings:
    """Load configuration from JSON file or environment variables."""

    data: Dict[str, Any] = {}
    if path:
        with Path(path).expanduser().open("r", encoding="utf-8") as handle:
            data = json.load(handle)

    access_key = data.get("access_key") or os.environ.get("UPBIT_ACCESS_KEY", "demo-access-key")
    secret_key = data.get("secret_key") or os.environ.get("UPBIT_SECRET_KEY", "demo-secret-key")

    strategy_conf = data.get("strategy", {}).get("moving_average", {})
    risk_conf = data.get("risk", {})

    settings = AppSettings(
        access_key=access_key,
        secret_key=secret_key,
        market=data.get("market", "KRW-BTC"),
        candle_interval=data.get("candle_interval", "minutes/1"),
        candle_count=data.get("candle_count", 200),
        order_amount=float(data.get("order_amount", 10_000)),
        poll_interval=float(data.get("poll_interval", 5.0)),
        strategy=StrategySettings(
            moving_average=MovingAverageConfig(
                short_window=strategy_conf.get("short_window", 5),
                long_window=strategy_conf.get("long_window", 20),
            )
        ),
        risk=RiskSettings(
            max_position=float(risk_conf.get("max_position", 0.001)),
            max_notional=float(risk_conf.get("max_notional", 500_000)),
        ),
    )
    return settings


__all__ = ["AppSettings", "load_settings"]
