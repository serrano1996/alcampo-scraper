import app.exceptions
from app.exceptions import (
    AlcampoScraperError,
    CooldownActiveError,
    OutboundRateLimitedError,
    UpstreamBlockedError,
    UpstreamThrottledError,
    UpstreamUnavailableError,
)


def test_upstream_unavailable_error_is_a_domain_error() -> None:
    error = UpstreamUnavailableError("boom")

    assert isinstance(error, AlcampoScraperError)
    assert error.reason == "boom"


def test_exceptions_module_does_not_import_httpx() -> None:
    assert "httpx" not in vars(app.exceptions)


def test_upstream_blocked_error_is_an_upstream_unavailable_error() -> None:
    error = UpstreamBlockedError("waf")

    assert isinstance(error, UpstreamUnavailableError)
    assert error.reason == "waf"


def test_cooldown_active_error_is_an_upstream_unavailable_error() -> None:
    assert isinstance(CooldownActiveError("cooldown"), UpstreamUnavailableError)


def test_foreseen_degradations_share_a_throttled_parent() -> None:
    # One family logged as WARNING by the 502 handler (spec 008 plan-D5).
    assert issubclass(UpstreamThrottledError, UpstreamUnavailableError)
    assert issubclass(CooldownActiveError, UpstreamThrottledError)
    assert issubclass(OutboundRateLimitedError, UpstreamThrottledError)
    assert not issubclass(UpstreamBlockedError, UpstreamThrottledError)
