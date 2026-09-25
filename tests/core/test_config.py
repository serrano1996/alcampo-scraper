import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings

REQUIRED = {
    "ALCAMPO_BASE_URL": "https://alcampo.test",
    "REDIS_URL": "redis://localhost:6379/0",
}
OPTIONAL = [
    "CACHE_TTL_SECONDS",
    "RETRY_MAX_ATTEMPTS",
    "RETRY_BASE_DELAY",
    "RETRY_JITTER_MAX_S",
    "WAF_COOLDOWN_SECONDS",
    "LOG_LEVEL",
]


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


def test_retry_jitter_max_s_defaults_to_0_3(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)

    assert Settings(_env_file=None).retry_jitter_max_s == 0.3


@pytest.mark.parametrize(("raw", "expected"), [("0", 0.0), ("1.5", 1.5)])
def test_retry_jitter_max_s_accepts_non_negative_values(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: float
) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("RETRY_JITTER_MAX_S", raw)

    assert Settings(_env_file=None).retry_jitter_max_s == expected


def test_negative_retry_jitter_max_s_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("RETRY_JITTER_MAX_S", "-0.1")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_waf_cooldown_seconds_defaults_to_180(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)

    assert Settings(_env_file=None).waf_cooldown_seconds == 180


@pytest.mark.parametrize(("raw", "expected"), [("0", 0), ("240", 240)])
def test_waf_cooldown_seconds_accepts_non_negative_values(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: int
) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("WAF_COOLDOWN_SECONDS", raw)

    assert Settings(_env_file=None).waf_cooldown_seconds == expected


def test_negative_waf_cooldown_seconds_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("WAF_COOLDOWN_SECONDS", "-1")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
