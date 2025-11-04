"""Application settings and environment loading helpers."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration object loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    access_key: str = Field(validation_alias="UPBIT_ACCESS_KEY")
    secret_key: str = Field(validation_alias="UPBIT_SECRET_KEY")
    market: str = Field(default="KRW-BTC", validation_alias="UPBIT_MARKET")
    strategy: str = Field(default="ma_crossover", validation_alias="UPBIT_STRATEGY")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    use_sandbox: bool = Field(default=False, validation_alias="UPBIT_USE_SANDBOX")
    strategy_components: str | None = Field(
        default=None,
        validation_alias="UPBIT_STRATEGY_COMPONENTS",
    )
    max_daily_loss_pct: float = Field(
        default=3.0,
        validation_alias="RISK_MAX_DAILY_LOSS_PCT",
    )
    max_position_pct: float = Field(
        default=5.0,
        validation_alias="RISK_MAX_POSITION_PCT",
    )
    max_open_positions: int = Field(
        default=3,
        validation_alias="RISK_MAX_OPEN_POSITIONS",
    )
    min_balance_krw: float = Field(
        default=10000.0,
        validation_alias="RISK_MIN_BALANCE_KRW",
    )
    slack_webhook_url: str | None = Field(
        default=None,
        validation_alias="SLACK_WEBHOOK_URL",
    )
    telegram_bot_token: str | None = Field(
        default=None,
        validation_alias="TELEGRAM_BOT_TOKEN",
    )
    telegram_chat_id: str | None = Field(
        default=None,
        validation_alias="TELEGRAM_CHAT_ID",
    )

    @field_validator("market")
    @classmethod
    def _validate_market(cls, value: str) -> str:  # noqa: D417
        if "-" not in value:
            raise ValueError("Market must be in the form BASE-QUOTE, e.g. KRW-BTC.")
        return value.upper()


def _resolve_env_file(env_path: str | None) -> Path | None:
    if env_path:
        explicit = Path(env_path)
        if explicit.is_file():
            return explicit
        raise FileNotFoundError(f"Env file not found: {env_path}")
    cwd_env = Path(".env")
    if cwd_env.is_file():
        return cwd_env
    return None


def load_settings(env_path: str | None = None, reload: bool = False) -> Settings:
    """Load configuration, optionally pointing to a specific .env file."""

    env_file = _resolve_env_file(env_path)
    if env_file:
        load_dotenv(env_file)

    if reload:
        _load_settings_cached.cache_clear()

    return _load_settings_cached()


@lru_cache
def _load_settings_cached() -> Settings:
    return Settings()  # type: ignore[call-arg]


__all__ = ["Settings", "load_settings"]
