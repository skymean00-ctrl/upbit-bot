"""Risk management utilities for live trading."""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

LOGGER = logging.getLogger(__name__)


@dataclass
class RiskConfig:
    max_daily_loss_pct: float = 3.0
    max_position_pct: float = 5.0
    max_open_positions: int = 3
    min_balance_krw: float = 10000.0
    slippage_pct: float = 0.001


@dataclass
class DailyRiskState:
    date: dt.date
    realized_pnl_pct: float = 0.0
    open_positions: dict[str, float] = field(default_factory=dict)

    def reset_if_needed(self) -> None:
        today = dt.date.today()
        if self.date != today:
            self.date = today
            self.realized_pnl_pct = 0.0
            self.open_positions.clear()


class RiskManager:
    """Tracks drawdown and prevents trading when limits are exceeded."""

    def __init__(
        self,
        balance_fetcher: Callable[[], float],
        config: RiskConfig | None = None,
    ) -> None:
        self.balance_fetcher = balance_fetcher
        self.config = config or RiskConfig()
        self.state = DailyRiskState(date=dt.date.today())

    def can_open_position(self, market: str) -> bool:
        self.state.reset_if_needed()

        if self.state.realized_pnl_pct <= -self.config.max_daily_loss_pct:
            LOGGER.warning(
                "Daily loss limit reached (%.2f%%). Blocking new trades.",
                self.state.realized_pnl_pct,
            )
            return False

        if len(self.state.open_positions) >= self.config.max_open_positions:
            LOGGER.warning("Maximum open positions reached: %d", len(self.state.open_positions))
            return False

        balance = self.balance_fetcher()
        if balance < self.config.min_balance_krw:
            LOGGER.warning(
                "Insufficient KRW balance %.0f < %.0f",
                balance,
                self.config.min_balance_krw,
            )
            return False

        return True

    def register_entry(self, market: str, stake_pct: float) -> None:
        self.state.reset_if_needed()
        self.state.open_positions[market] = stake_pct
        LOGGER.debug("Registered entry %s @ %.2f%% stake", market, stake_pct)

    def register_exit(self, market: str, pnl_pct: float) -> None:
        self.state.reset_if_needed()
        self.state.open_positions.pop(market, None)
        self.state.realized_pnl_pct += pnl_pct
        LOGGER.debug(
            "Registered exit %s: pnl %.2f%%, cumulative %.2f%%",
            market,
            pnl_pct,
            self.state.realized_pnl_pct,
        )


class PositionSizer:
    """Calculates order sizing based on account balance and risk parameters."""

    def __init__(
        self,
        balance_fetcher: Callable[[], float],
        config: RiskConfig | None = None,
    ) -> None:
        self.balance_fetcher = balance_fetcher
        self.config = config or RiskConfig()

    def krw_stake(self) -> float:
        balance = self.balance_fetcher()
        stake = balance * (self.config.max_position_pct / 100)
        return max(stake, 0.0)
