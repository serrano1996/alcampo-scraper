import app.exceptions
from app.exceptions import AlcampoScraperError, UpstreamBlockedError, UpstreamUnavailableError


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
