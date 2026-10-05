import logging
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.dependencies import get_product_service
from app.core.security import require_api_key
from app.exceptions import (
    CooldownActiveError,
    OutboundRateLimitedError,
    PageOutOfRangeError,
    PostalCodeNotServedError,
    UpstreamBlockedError,
    UpstreamUnavailableError,
)
from app.main import create_app
from app.models.product import Product, ProductQuery, ProductSearchResponse, SearchMetadata


class FakeService:
    def __init__(
        self, response: ProductSearchResponse | None = None, error: Exception | None = None
    ):
        self.response = response
        self.error = error

    async def search(self, query: ProductQuery) -> ProductSearchResponse:
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def make_response() -> ProductSearchResponse:
    return ProductSearchResponse(
        search=SearchMetadata(
            postal_code="28001",
            term="leche",
            warehouse="5",
            strategy_used="api",
            scraped_at=datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC),
            total_results=1,
            page=1,
            page_size=50,
            total_pages=1,
        ),
        products=[
            Product(
                id="54180",
                name="Leche",
                price=5.28,
                price_format="0.88 €/L",
                image_url="https://img.test/54180.jpg",
                category="Leche semidesnatada",
            )
        ],
    )


def make_client(service: FakeService) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_product_service] = lambda: service
    # These tests exercise the route, not auth (covered in tests/integration/test_auth.py).
    app.dependency_overrides[require_api_key] = lambda: None
    return TestClient(app)


def test_valid_request_returns_service_response() -> None:
    client = make_client(FakeService(response=make_response()))

    response = client.get("/api/v1/products", params={"postal_code": "28001", "term": "leche"})

    assert response.status_code == 200
    assert response.json()["search"]["term"] == "leche"
    assert response.json()["products"][0]["id"] == "54180"


def test_missing_term_returns_422() -> None:
    client = make_client(FakeService(response=make_response()))

    response = client.get("/api/v1/products", params={"postal_code": "28001"})

    assert response.status_code == 422


def test_term_over_100_chars_returns_422() -> None:
    # spec 009 RF-3: max 100, as in Mercadona (was 50).
    client = make_client(FakeService(response=make_response()))

    response = client.get("/api/v1/products", params={"postal_code": "28001", "term": "a" * 101})

    assert response.status_code == 422


def test_upstream_error_returns_502_without_leaking_the_reason() -> None:
    client = make_client(FakeService(error=UpstreamUnavailableError("secret upstream body")))

    response = client.get("/api/v1/products", params={"postal_code": "28001", "term": "leche"})

    assert response.status_code == 502
    assert response.json() == {"detail": "Upstream service unavailable"}
    assert "secret" not in response.text


def test_upstream_blocked_error_returns_the_standard_502() -> None:
    client = make_client(FakeService(error=UpstreamBlockedError("waf")))

    response = client.get("/api/v1/products", params={"postal_code": "28001", "term": "leche"})

    assert response.status_code == 502
    assert response.json() == {"detail": "Upstream service unavailable"}
    assert "waf" not in response.text


SEARCH = {"postal_code": "28001", "term": "leche"}


def records_at(caplog: pytest.LogCaptureFixture, level: int) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno == level]


def test_upstream_502_is_logged_as_error_with_reason_and_search(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = make_client(FakeService(error=UpstreamUnavailableError("upstream returned 503")))

    response = client.get("/api/v1/products", params=SEARCH)

    assert response.status_code == 502
    [error] = records_at(caplog, logging.ERROR)
    message = error.getMessage()
    assert "upstream returned 503" in message
    assert "'28001'" in message
    assert "'leche'" in message


def test_cooldown_502_is_only_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    client = make_client(FakeService(error=CooldownActiveError("WAF cooldown active")))

    response = client.get("/api/v1/products", params=SEARCH)

    assert response.status_code == 502
    assert response.json() == {"detail": "Upstream service unavailable"}
    assert len(records_at(caplog, logging.WARNING)) == 1
    assert records_at(caplog, logging.ERROR) == []


def test_rate_limited_502_is_only_a_warning_with_its_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = make_client(FakeService(error=OutboundRateLimitedError("outbound rate limit reached")))

    response = client.get("/api/v1/products", params=SEARCH)

    assert response.status_code == 502
    assert response.json() == {"detail": "Upstream service unavailable"}
    [warning] = records_at(caplog, logging.WARNING)
    assert "outbound rate limit reached" in warning.getMessage()
    assert records_at(caplog, logging.ERROR) == []


def test_waf_challenge_502_is_logged_as_error(caplog: pytest.LogCaptureFixture) -> None:
    client = make_client(FakeService(error=UpstreamBlockedError("WAF challenge")))

    client.get("/api/v1/products", params=SEARCH)

    assert len(records_at(caplog, logging.ERROR)) == 1


# --- spec 007 RF-4: postal code not served -------------------------------------


def test_postal_code_not_served_returns_404_logged_as_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    client = make_client(FakeService(error=PostalCodeNotServedError("51001")))

    response = client.get("/api/v1/products", params={"postal_code": "51001", "term": "agua"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Postal code not served by Alcampo"}
    assert response.headers["X-Request-ID"]
    served = [r for r in caplog.records if r.name == "app.main"]
    assert [r.levelno for r in served] == [logging.INFO]
    assert "'51001'" in served[0].getMessage()


# --- spec 009 RF-6: a page past the last one ------------------------------------


def test_page_out_of_range_returns_404_logged_as_info(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    client = make_client(FakeService(error=PageOutOfRangeError(page=7)))

    response = client.get(
        "/api/v1/products", params={"postal_code": "28001", "term": "quinoa", "page": 7}
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Page out of range"}
    assert response.headers["X-Request-ID"]
    served = [r for r in caplog.records if r.name == "app.main"]
    assert [r.levelno for r in served] == [logging.INFO]
