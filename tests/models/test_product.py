from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.product import (
    MAX_PAGE,
    Product,
    ProductQuery,
    ProductSearchResponse,
    SearchMetadata,
)


def test_term_is_stripped() -> None:
    query = ProductQuery(postal_code="28001", term="  leche ")

    assert query.term == "leche"


@pytest.mark.parametrize(
    "term",
    [
        "   ",
        "a" * 101,  # spec 009: max 100, as in Mercadona (was 51 → 422)
    ],
)
def test_invalid_term_raises(term: str) -> None:
    with pytest.raises(ValidationError):
        ProductQuery(postal_code="28001", term=term)


def test_term_at_max_length_is_valid() -> None:
    query = ProductQuery(postal_code="28001", term="a" * 100)  # spec 009 (was 50)

    assert query.term == "a" * 100


def test_empty_postal_code_raises() -> None:
    with pytest.raises(ValidationError):
        ProductQuery(postal_code="", term="leche")


def test_price_format_is_the_only_optional_product_field() -> None:
    # spec 009 RF-11: image_url and category are never null, as in Mercadona;
    # price_format stays optional in both. Replaces spec 001's
    # test_product_optional_fields_accept_none.
    product = Product(
        id="54180",
        name="Leche",
        price=5.28,
        price_format=None,
        image_url="https://img.test/54180.jpg",
        category="Leche semidesnatada",
    )

    assert product.price_format is None


@pytest.mark.parametrize("field", ["image_url", "category"])
def test_image_url_and_category_cannot_be_null(field: str) -> None:
    values = {
        "id": "54180",
        "name": "Leche",
        "price": 5.28,
        "price_format": None,
        "image_url": "https://img.test/54180.jpg",
        "category": "Leche semidesnatada",
    }

    with pytest.raises(ValidationError):
        Product(**{**values, field: None})


def test_search_response_serializes_scraped_at_with_z_suffix() -> None:
    response = ProductSearchResponse(
        search=SearchMetadata(
            postal_code="28001",
            term="leche",
            warehouse="5",
            strategy_used="api",
            scraped_at=datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC),
            total_results=0,
            page=1,
            page_size=50,
            total_pages=0,
        ),
        products=[],
    )

    payload = response.model_dump(mode="json")

    assert payload["search"]["scraped_at"] == "2026-09-24T10:00:00Z"


# --- spec 007 RF-1: postal codes are exactly 5 digits --------------------------

# Unicode digits that `\d` would accept: only ASCII 0-9 are valid.
FULLWIDTH_28001 = "".join(chr(0xFF10 + int(digit)) for digit in "28001")


@pytest.mark.parametrize(
    "postal_code", ["2800", "280011", "abcde", "28 01", "2800a", FULLWIDTH_28001]
)
def test_postal_code_that_is_not_5_digits_raises(postal_code: str) -> None:
    with pytest.raises(ValidationError):
        ProductQuery(postal_code=postal_code, term="leche")


def test_postal_code_is_stripped_before_validation() -> None:
    assert ProductQuery(postal_code=" 28001 ", term="leche").postal_code == "28001"


# --- spec 009 RF-1, RF-8: page and page_size, as in Mercadona -----------------


def test_page_and_page_size_default_to_the_first_page_of_50() -> None:
    query = ProductQuery(postal_code="28001", term="leche")

    assert (query.page, query.page_size) == (1, 50)


@pytest.mark.parametrize(
    ("field", "value"),
    [("page", 0), ("page", MAX_PAGE + 1), ("page_size", 0), ("page_size", 101)],
)
def test_page_or_page_size_out_of_range_raises(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        ProductQuery(postal_code="28001", term="leche", **{field: value})


def test_the_last_reachable_page_and_the_largest_page_size_are_valid() -> None:
    query = ProductQuery(postal_code="28001", term="leche", page=MAX_PAGE, page_size=100)

    assert (query.page, query.page_size) == (20, 100)
