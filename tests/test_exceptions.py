import app.exceptions
from app.exceptions import AlcampoScraperError, UpstreamUnavailableError


def test_upstream_unavailable_error_is_a_domain_error() -> None:
    error = UpstreamUnavailableError("boom")

    assert isinstance(error, AlcampoScraperError)
    assert error.reason == "boom"


def test_exceptions_module_does_not_import_httpx() -> None:
    assert "httpx" not in vars(app.exceptions)
