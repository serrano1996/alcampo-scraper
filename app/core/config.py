"""Application settings loaded from environment variables (and an optional `.env` file)."""

from functools import lru_cache
from typing import Annotated, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
    # Outbound protection (spec 008). The rate-limit default is an estimate: the
    # WAF threshold for search is unknown (Fase 0 saw blocks on another endpoint).
    # 0 disables the limit (RF-6).
    alcampo_rate_limit: int = Field(default=20, ge=0)
    alcampo_rate_window_seconds: int = Field(default=60, ge=1)
    search_timeout_seconds: float = Field(default=15, gt=0)
    # Cap of the growing cooldown, and how long a challenge counts as "recent" (RF-8).
    waf_cooldown_max_seconds: int = Field(default=900, ge=0)
    log_level: str = "INFO"
    # Comma-separated in the environment. `NoDecode` stops pydantic-settings from
    # parsing it as JSON (a plain frozenset raises SettingsError on "a,b"), and
    # `repr=False` keeps the tokens out of any printed or logged Settings
    # (spec 004 RF-7, RF-12, plan-D1). Empty = nobody authenticates (RF-8).
    api_keys: Annotated[frozenset[str], NoDecode] = Field(default=frozenset(), repr=False)

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        """Accept any case, fail at startup on unknown levels (spec 003 RF-2, plan-D11)."""
        level = value.strip().upper()
        if level not in LOG_LEVELS:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(LOG_LEVELS)}, got {value!r}")
        return level

    @field_validator("api_keys", mode="before")
    @classmethod
    def _split_api_keys(cls, value: object) -> object:
        """`" a , ,b "` -> `{"a", "b"}`: trim each entry and drop empty ones (RF-7)."""
        if isinstance(value, str):
            return frozenset(key.strip() for key in value.split(",") if key.strip())
        return value

    @model_validator(mode="after")
    def _check_cooldown_bounds(self) -> Self:
        """The growing cooldown can never start above its own cap (spec 008 RF-10)."""
        if self.waf_cooldown_max_seconds < self.waf_cooldown_seconds:
            raise ValueError(
                "WAF_COOLDOWN_MAX_SECONDS must be >= WAF_COOLDOWN_SECONDS, got "
                f"{self.waf_cooldown_max_seconds} < {self.waf_cooldown_seconds}"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance."""
    return Settings()
