import app.exceptions
from app.exceptions import (
    AlcampoScraperError,
    CooldownActiveError,
    OutboundRateLimitedError,
    PostalCodeNotServedError,
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


def test_postal_code_not_served_is_a_domain_error_but_not_an_upstream_failure() -> None:
    # A 404 for the client, not a 502: Alcampo answered, it just does not serve
    # that postal code (spec 007 RF-4, spec-D8).
    error = PostalCodeNotServedError("35001")

    assert isinstance(error, AlcampoScraperError)
    assert not isinstance(error, UpstreamUnavailableError)
    assert error.postal_code == "35001"


def test_upstream_status_code_is_optional() -> None:
    assert UpstreamUnavailableError("boom").status_code is None
    assert UpstreamUnavailableError("gone", status_code=410).status_code == 410
