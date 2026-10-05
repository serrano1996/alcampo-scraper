import json
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.mappers.product_mapper import map_search
from app.models.alcampo import AlcampoProduct, AlcampoSearchResponse

FIXTURES = Path(__file__).parents[1] / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def first_raw_product() -> dict:
    raw = load_fixture("alcampo_search_leche.json")
    return raw["productGroups"][0]["decoratedProducts"][0]


def test_search_response_parses_real_fixture() -> None:
    raw = load_fixture("alcampo_search_leche.json")

    response = AlcampoSearchResponse.model_validate(raw)

    assert len(response.product_groups) == 1
    assert len(response.product_groups[0].decorated_products) == 3


def test_search_response_without_product_groups_is_invalid() -> None:
    with pytest.raises(ValidationError):
        AlcampoSearchResponse.model_validate({"metadata": {}})


def test_no_results_fixture_has_empty_product_groups() -> None:
    raw = load_fixture("alcampo_search_no_results.json")

    response = AlcampoSearchResponse.model_validate(raw)

    assert response.product_groups == []


def test_product_parses_real_fixture() -> None:
    product = AlcampoProduct.model_validate(first_raw_product())

    assert product.retailer_product_id == "54180"
    assert product.price.amount == "5.28"
    assert product.unit_price is not None
    assert product.unit_price.price is not None
    assert product.unit_price.price.amount == "0.88"
    assert product.unit_price.unit_name == "PER_LITRE"
    assert product.category_path == [
        "Leche, Huevos, Lácteos, Yogures y Bebidas vegetales",
        "Leche",
        "Leche semidesnatada",
    ]


def test_product_ignores_unknown_fields() -> None:
    raw = {**first_raw_product(), "quantityInBasket": 0, "promotions": []}

    product = AlcampoProduct.model_validate(raw)

    assert product.name == first_raw_product()["name"]


@pytest.mark.parametrize("amount", ["abc", "", "1.2.3"])
def test_invalid_price_amount_raises(amount: str) -> None:
    raw = {**first_raw_product(), "price": {"amount": amount, "currency": "EUR"}}

    with pytest.raises(ValidationError):
        AlcampoProduct.model_validate(raw)


def test_product_without_category_path_is_invalid() -> None:
    # spec 009 RF-11: the category is never null (as in Mercadona), so a product
    # without it is malformed. Replaces spec 001's ..._defaults_to_empty_list.
    raw = dict(first_raw_product())
    del raw["categoryPath"]

    with pytest.raises(ValidationError):
        AlcampoProduct.model_validate(raw)


def test_product_without_unit_price_is_none() -> None:
    raw = dict(first_raw_product())
    del raw["unitPrice"]

    product = AlcampoProduct.model_validate(raw)

    assert product.unit_price is None


# --- spec 009 RF-4, RF-7: cursor and category counts (verified live, spec §10) ---


def test_search_response_reads_the_next_page_token_and_category_counts() -> None:
    raw = AlcampoSearchResponse.model_validate(load_fixture("alcampo_search_leche.json"))

    assert raw.metadata is not None
    assert raw.metadata.next_page_token == "cf78cbe4-2f2e-4db6-b72a-617e8d6a9350"
    assert raw.additional_page_info is not None
    assert raw.additional_page_info.categories[0].product_count == 535


def test_last_page_and_missing_counts_are_optional() -> None:
    # The last page arrives with `metadata: {}` and no token (spec §10, `quinoa`).
    raw = AlcampoSearchResponse.model_validate({"productGroups": [], "metadata": {}})

    assert raw.metadata is not None
    assert raw.metadata.next_page_token is None
    assert raw.additional_page_info is None


# --- spec 011 RF-7: fields the app does not use can change freely ---------------

UNUSED_REMOVED = ("brand", "productId", "promotions", "available", "images")
UNUSED_RETYPED = {"available": "yes", "brand": 42, "promotions": {"changed": True}}


def real_products_with(change: Callable[[dict], None]) -> AlcampoSearchResponse:
    """The real `leche` response with `change` applied to every product."""
    body = load_fixture("alcampo_search_leche.json")
    for group in body["productGroups"]:
        for product in group["decoratedProducts"]:
            change(product)
    return AlcampoSearchResponse.model_validate(body)


def remove_unused(product: dict) -> None:
    for name in UNUSED_REMOVED:
        product.pop(name, None)


def retype_unused(product: dict) -> None:
    product.update(UNUSED_RETYPED)


@pytest.mark.parametrize("change", [remove_unused, retype_unused], ids=["removed", "retyped"])
def test_unused_fields_do_not_change_the_products(change: Callable[[dict], None]) -> None:
    original = map_search(
        AlcampoSearchResponse.model_validate(load_fixture("alcampo_search_leche.json"))
    )

    assert map_search(real_products_with(change)) == original
    assert len(original) == 3
