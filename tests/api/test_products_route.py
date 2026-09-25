from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.core.dependencies import get_product_service
from app.exceptions import UpstreamUnavailableError
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
        ),
        products=[
            Product(
                id="54180",
                name="Leche",
                price=5.28,
                price_format="0.88 €/L",
                image_url=None,
                category=None,
            )
        ],
    )


def make_client(service: FakeService) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_product_service] = lambda: service
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


def test_term_over_50_chars_returns_422() -> None:
    client = make_client(FakeService(response=make_response()))

    response = client.get("/api/v1/products", params={"postal_code": "28001", "term": "a" * 51})

    assert response.status_code == 422


def test_upstream_error_returns_502_without_leaking_the_reason() -> None:
    client = make_client(FakeService(error=UpstreamUnavailableError("secret upstream body")))

    response = client.get("/api/v1/products", params={"postal_code": "28001", "term": "leche"})

    assert response.status_code == 502
    assert response.json() == {"detail": "Upstream service unavailable"}
    assert "secret" not in response.text
