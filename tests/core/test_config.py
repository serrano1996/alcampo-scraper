import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings

REQUIRED = {
    "ALCAMPO_BASE_URL": "https://alcampo.test",
    "REDIS_URL": "redis://localhost:6379/0",
}
OPTIONAL = ["CACHE_TTL_SECONDS", "RETRY_MAX_ATTEMPTS", "RETRY_BASE_DELAY", "LOG_LEVEL"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [*REQUIRED, *OPTIONAL]:
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()


def set_required(monkeypatch: pytest.MonkeyPatch, *, skip: str | None = None) -> None:
    for name, value in REQUIRED.items():
        if name != skip:
            monkeypatch.setenv(name, value)


@pytest.mark.parametrize("missing", list(REQUIRED))
def test_missing_required_variable_fails(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    set_required(monkeypatch, skip=missing)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert missing.lower() in str(exc_info.value)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.alcampo_base_url == "https://alcampo.test"
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.cache_ttl_seconds == 3600
    assert settings.retry_max_attempts == 3
    assert settings.retry_base_delay == 0.5
    assert settings.log_level == "INFO"


def test_environment_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("RETRY_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("RETRY_BASE_DELAY", "0")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")

    settings = Settings(_env_file=None)

    assert settings.cache_ttl_seconds == 60
    assert settings.retry_max_attempts == 5
    assert settings.retry_base_delay == 0
    assert settings.log_level == "DEBUG"


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)

    assert get_settings() is get_settings()
