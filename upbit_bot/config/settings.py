from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StrategyConfig(BaseSettings):
    name: str = "ma_crossover"
    config: dict = Field(default_factory=dict)


class Settings(BaseSettings):
    """Central configuration object loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    access_key: str = Field(
        ...,
        alias="UPBIT_ACCESS_KEY",
        env=("UPBIT_ACCESS_KEY", "UPBIT_API_KEY"),
    )
    secret_key: str = Field(
        ...,
        alias="UPBIT_SECRET_KEY",
        env=("UPBIT_SECRET_KEY", "UPBIT_API_SECRET"),
    )


    # 새로 추가된 combined_strategies 필드
    combined_strategies: list[StrategyConfig] | None = None

    # 기존 strategy 필드 (호환성을 위해 유지)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    strategy_components: str | None = Field(None, env="UPBIT_STRATEGY_COMPONENTS")

    market: str = Field("KRW-BTC", env="UPBIT_MARKET")
    max_daily_loss_pct: float = Field(3.0, env="UPBIT_MAX_DAILY_LOSS_PCT")
    max_position_pct: float = Field(5.0, env="UPBIT_MAX_POSITION_PCT")
    max_open_positions: int = Field(3, env="UPBIT_MAX_OPEN_POSITIONS")
    min_balance_krw: float = Field(10000.0, env="UPBIT_MIN_BALANCE_KRW")
    order_amount_pct: float = Field(3.0, env="UPBIT_ORDER_AMOUNT_PCT")  # 1건당 매수 퍼센트 (기본값: 3%)
    slack_webhook_url: str | None = Field(None, env="UPBIT_SLACK_WEBHOOK_URL")
    telegram_bot_token: str | None = Field(None, env="UPBIT_TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(None, env="UPBIT_TELEGRAM_CHAT_ID")

    @property
    def upbit_api_key(self) -> str:
        return self.access_key

    @property
    def upbit_secret_key(self) -> str:
        return self.secret_key

    # 여기에 RiskConfig 등 다른 설정 필드들이 있을 수 있습니다.
    # 예시:
    # risk: RiskConfig = Field(default_factory=RiskConfig)


def load_settings(env_path: str | Path | None = None) -> Settings:
    """Load settings from environment variables or .env file.
    
    Args:
        env_path: Optional path to .env file. If None, uses default .env file.
    
    Returns:
        Settings instance loaded from environment variables.
    """
    if env_path:
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()  # .env 파일 로드
    return Settings()
