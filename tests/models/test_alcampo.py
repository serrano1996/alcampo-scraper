import json
from pathlib import Path

import pytest
from pydantic import ValidationError

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


def test_product_without_category_path_defaults_to_empty_list() -> None:
    raw = dict(first_raw_product())
    del raw["categoryPath"]

    product = AlcampoProduct.model_validate(raw)

    assert product.category_path == []


def test_product_without_unit_price_is_none() -> None:
    raw = dict(first_raw_product())
    del raw["unitPrice"]

    product = AlcampoProduct.model_validate(raw)

    assert product.unit_price is None
