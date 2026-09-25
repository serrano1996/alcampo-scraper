from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.product import Product, ProductQuery, ProductSearchResponse, SearchMetadata


def test_term_is_stripped() -> None:
    query = ProductQuery(postal_code="28001", term="  leche ")

    assert query.term == "leche"


@pytest.mark.parametrize(
    "term",
    [
        "   ",
        "a" * 51,
    ],
)
def test_invalid_term_raises(term: str) -> None:
    with pytest.raises(ValidationError):
        ProductQuery(postal_code="28001", term=term)


def test_term_at_max_length_is_valid() -> None:
    query = ProductQuery(postal_code="28001", term="a" * 50)

    assert query.term == "a" * 50


def test_empty_postal_code_raises() -> None:
    with pytest.raises(ValidationError):
        ProductQuery(postal_code="", term="leche")


def test_product_optional_fields_accept_none() -> None:
    product = Product(
        id="54180",
        name="Leche",
        price=5.28,
        price_format=None,
        image_url=None,
        category=None,
    )

    assert product.price_format is None
    assert product.image_url is None
    assert product.category is None


def test_search_response_serializes_scraped_at_with_z_suffix() -> None:
    response = ProductSearchResponse(
        search=SearchMetadata(
            postal_code="28001",
            term="leche",
            warehouse="5",
            strategy_used="api",
            scraped_at=datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC),
            total_results=0,
        ),
        products=[],
    )

    payload = response.model_dump(mode="json")

    assert payload["search"]["scraped_at"] == "2026-09-24T10:00:00Z"
