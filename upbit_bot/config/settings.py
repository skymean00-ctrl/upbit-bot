from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StrategyConfig(BaseSettings):
    name: str
    config: dict = Field(default_factory=dict)


class Settings(BaseSettings):
    """Central configuration object loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    upbit_api_key: str = Field(..., env="UPBIT_API_KEY")
    upbit_secret_key: str = Field(..., env="UPBIT_SECRET_KEY")

    # 새로 추가된 combined_strategies 필드
    combined_strategies: list[StrategyConfig] | None = None

    # 기존 strategy 필드 (호환성을 위해 유지)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)

    # 여기에 RiskConfig 등 다른 설정 필드들이 있을 수 있습니다.
    # 예시:
    # risk: RiskConfig = Field(default_factory=RiskConfig)


@lru_cache
def load_settings() -> Settings:
    """Load settings from environment variables or .env file."""
    load_dotenv()  # .env 파일 로드
    return Settings()

