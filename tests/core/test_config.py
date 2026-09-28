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
    "API_KEYS",
    "ALCAMPO_RATE_LIMIT",
    "ALCAMPO_RATE_WINDOW_SECONDS",
    "SEARCH_TIMEOUT_SECONDS",
    "WAF_COOLDOWN_MAX_SECONDS",
    "REDIS_TIMEOUT_SECONDS",
    "REGION_CACHE_TTL_SECONDS",
    "REGION_NEGATIVE_CACHE_TTL_SECONDS",
    "REGION_RESOLUTION_LIMIT",
    "REGION_RESOLUTION_WINDOW_SECONDS",
    "SESSION_MAX_AGE_SECONDS",
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


@pytest.mark.parametrize(("raw", "expected"), [("debug", "DEBUG"), ("Warning", "WARNING")])
def test_log_level_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("LOG_LEVEL", raw)

    assert Settings(_env_file=None).log_level == expected


def test_unknown_log_level_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_log_level_defaults_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)

    assert Settings(_env_file=None).log_level == "INFO"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" a , ,b ", frozenset({"a", "b"})),
        (" , ,", frozenset()),
        ("single", frozenset({"single"})),
    ],
)
def test_api_keys_are_split_trimmed_and_cleaned(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: frozenset[str]
) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("API_KEYS", raw)

    assert Settings(_env_file=None).api_keys == expected


def test_api_keys_default_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)

    assert Settings(_env_file=None).api_keys == frozenset()


def test_api_keys_are_hidden_from_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("API_KEYS", "secret-one,secret-two")

    rendered = repr(Settings(_env_file=None))

    assert "secret-one" not in rendered
    assert "secret-two" not in rendered


# --- spec 008: outbound protection -------------------------------------------


def test_outbound_protection_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.alcampo_rate_limit == 20
    assert settings.alcampo_rate_window_seconds == 60
    assert settings.search_timeout_seconds == 15
    assert settings.waf_cooldown_max_seconds == 900


def test_rate_limit_zero_disables_it(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("ALCAMPO_RATE_LIMIT", "0")

    assert Settings(_env_file=None).alcampo_rate_limit == 0


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("ALCAMPO_RATE_LIMIT", "-1"),
        ("ALCAMPO_RATE_WINDOW_SECONDS", "0"),
        ("SEARCH_TIMEOUT_SECONDS", "0"),
        ("SEARCH_TIMEOUT_SECONDS", "-1"),
        ("WAF_COOLDOWN_MAX_SECONDS", "-1"),
    ],
)
def test_invalid_outbound_protection_values_fail(
    monkeypatch: pytest.MonkeyPatch, name: str, raw: str
) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv(name, raw)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert name.lower() in str(exc_info.value)


def test_cooldown_max_below_base_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("WAF_COOLDOWN_SECONDS", "180")
    monkeypatch.setenv("WAF_COOLDOWN_MAX_SECONDS", "100")

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert "WAF_COOLDOWN_MAX_SECONDS" in str(exc_info.value)


@pytest.mark.parametrize("maximum", ["0", "900"])
def test_disabled_cooldown_accepts_any_max(monkeypatch: pytest.MonkeyPatch, maximum: str) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("WAF_COOLDOWN_SECONDS", "0")
    monkeypatch.setenv("WAF_COOLDOWN_MAX_SECONDS", maximum)

    assert Settings(_env_file=None).waf_cooldown_max_seconds == int(maximum)


def test_cooldown_max_equal_to_base_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("WAF_COOLDOWN_SECONDS", "300")
    monkeypatch.setenv("WAF_COOLDOWN_MAX_SECONDS", "300")

    assert Settings(_env_file=None).waf_cooldown_max_seconds == 300


# --- spec 007: region resolution and Redis -------------------------------------


def test_region_and_redis_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)

    settings = Settings(_env_file=None)

    assert settings.redis_timeout_seconds == 2
    assert settings.region_cache_ttl_seconds == 604800  # 7 days
    assert settings.region_negative_cache_ttl_seconds == 3600
    assert settings.region_resolution_limit == 2
    assert settings.region_resolution_window_seconds == 600
    assert settings.session_max_age_seconds == 3000  # below VISITORID's 1 h


def test_region_resolution_limit_zero_disables_it(monkeypatch: pytest.MonkeyPatch) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv("REGION_RESOLUTION_LIMIT", "0")

    assert Settings(_env_file=None).region_resolution_limit == 0


@pytest.mark.parametrize(
    ("name", "raw"),
    [
        ("REDIS_TIMEOUT_SECONDS", "0"),
        ("REDIS_TIMEOUT_SECONDS", "-1"),
        ("REGION_CACHE_TTL_SECONDS", "0"),
        ("REGION_NEGATIVE_CACHE_TTL_SECONDS", "0"),
        ("REGION_RESOLUTION_LIMIT", "-1"),
        ("REGION_RESOLUTION_WINDOW_SECONDS", "0"),
        ("SESSION_MAX_AGE_SECONDS", "0"),
    ],
)
def test_invalid_region_and_redis_values_fail(
    monkeypatch: pytest.MonkeyPatch, name: str, raw: str
) -> None:
    set_required(monkeypatch)
    monkeypatch.setenv(name, raw)

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    assert name.lower() in str(exc_info.value)
