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
    # Outbound protection (specs 008 and 010). Two windows shared through Redis:
    # a short one against bursts and a long one, because on 2026-10-01 the WAF
    # blocked 10 searches in ~15 min that a 1-minute window let through. The
    # defaults are NOT proven safe (spec 010 D2): they are tuned with the traffic
    # breakdown logged on every challenge. 0 disables a window.
    alcampo_rate_limit: int = Field(default=10, ge=0)
    alcampo_rate_window_seconds: int = Field(default=60, ge=1)
    alcampo_rate_limit_long: int = Field(default=30, ge=0)
    alcampo_rate_window_long_seconds: int = Field(default=900, ge=1)
    # Minimum gap between two requests of this process, plus random jitter, so a
    # region resolution (~10 requests) is not sent in 1-3 s (spec 010 RF-3). 0 disables it.
    alcampo_min_interval_ms: int = Field(default=500, ge=0)
    alcampo_interval_jitter_ms: int = Field(default=500, ge=0)
    search_timeout_seconds: float = Field(default=15, gt=0)
    # Each request to Alcampo: connect, read, write and pool (spec 012 RF-4).
    # 10, the value of the former constant (spec-D3).
    http_timeout_seconds: float = Field(default=10, gt=0)
    # Cap of the growing cooldown, and how long a challenge counts as "recent" (RF-8).
    waf_cooldown_max_seconds: int = Field(default=900, ge=0)
    # Redis (spec 007 RF-14): without timeouts a hung Redis hangs every request.
    redis_timeout_seconds: float = Field(default=2, gt=0)
    # After a Redis failure, skip it for this long and use the local fallbacks
    # straight away, instead of waiting a timeout per operation. 0 disables it
    # (spec 007 RF-18, plan-D13).
    redis_circuit_open_seconds: int = Field(default=10, ge=0)
    # Postal code -> region (spec 007). Resolving costs ~8 requests, one of them
    # the most WAF-sensitive (creating a delivery destination), hence the long TTL
    # and the strict limit on new resolutions; 0 disables the limit (spec-D2..D4).
    region_cache_ttl_seconds: int = Field(default=604800, ge=1)
    region_negative_cache_ttl_seconds: int = Field(default=3600, ge=1)
    region_resolution_limit: int = Field(default=2, ge=0)
    region_resolution_window_seconds: int = Field(default=600, ge=1)
    # A region's session is renewed past this age, below VISITORID's 1 h (spec-D6).
    session_max_age_seconds: int = Field(default=3000, ge=1)
    log_level: str = "INFO"
    # Comma-separated in the environment. `NoDecode` stops pydantic-settings from
    # parsing it as JSON (a plain frozenset raises SettingsError on "a,b"), and
    # `repr=False` keeps the tokens out of any printed or logged Settings
    # (spec 004 RF-7, RF-12, plan-D1); `exclude=True` keeps them out of
    # model_dump() and model_dump_json() too (spec 015 RF-3). Empty = nobody
    # authenticates (RF-8).
    api_keys: Annotated[frozenset[str], NoDecode] = Field(
        default=frozenset(), repr=False, exclude=True
    )

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
