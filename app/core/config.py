"""Application settings loaded from environment variables (and an optional `.env` file)."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Missing required variables fail at startup."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    alcampo_base_url: str
    redis_url: str
    cache_ttl_seconds: int = 3600
    retry_max_attempts: int = 3
    retry_base_delay: float = 0.5
    retry_jitter_max_s: float = Field(default=0.3, ge=0)
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
