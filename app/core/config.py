"""Application settings loaded from environment variables (and an optional `.env` file)."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class Settings(BaseSettings):
    """Runtime configuration. Missing required variables fail at startup."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    alcampo_base_url: str
    redis_url: str
    cache_ttl_seconds: int = 3600
    retry_max_attempts: int = 3
    retry_base_delay: float = 0.5
    retry_jitter_max_s: float = Field(default=0.3, ge=0)
    waf_cooldown_seconds: int = Field(default=180, ge=0)
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        """Accept any case, fail at startup on unknown levels (spec 003 RF-2, plan-D11)."""
        level = value.strip().upper()
        if level not in LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(LOG_LEVELS)}, got {value!r}")
        return level


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
