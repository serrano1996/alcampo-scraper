import json
from pathlib import Path

from app.mappers.product_mapper import map_search
from app.models.alcampo import AlcampoSearchResponse

FIXTURES = Path(__file__).parents[1] / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def first_raw_product() -> dict:
    raw = load_fixture("alcampo_search_leche.json")
    return raw["productGroups"][0]["decoratedProducts"][0]


def test_map_search_returns_products_in_order() -> None:
    raw = AlcampoSearchResponse.model_validate(load_fixture("alcampo_search_leche.json"))

    products = map_search(raw)

    assert [p.id for p in products] == ["54180", "54178", "53549"]


def test_map_search_with_no_results_returns_empty_list() -> None:
    raw = AlcampoSearchResponse.model_validate(load_fixture("alcampo_search_no_results.json"))

    assert map_search(raw) == []


def test_map_search_deduplicates_by_retailer_product_id_keeping_first() -> None:
    product = first_raw_product()
    duplicate = {**product, "name": "duplicate, should be discarded"}
    raw = AlcampoSearchResponse.model_validate(
        {
            "productGroups": [
                {"decoratedProducts": [product]},
                {"decoratedProducts": [duplicate]},
            ]
        }
    )

    products = map_search(raw)

    assert len(products) == 1
    assert products[0].name == product["name"]


def test_map_search_discards_malformed_products_and_keeps_the_rest() -> None:
    good = first_raw_product()
    missing_name = {**first_raw_product(), "retailerProductId": "1", "name": ""}
    missing_id = {**first_raw_product(), "retailerProductId": "", "name": "no id"}
    bad_price = {
        **first_raw_product(),
        "retailerProductId": "2",
        "name": "bad price",
        "price": {"amount": "abc", "currency": "EUR"},
    }
    raw = AlcampoSearchResponse.model_validate(
        {"productGroups": [{"decoratedProducts": [good, missing_name, missing_id, bad_price]}]}
    )

    products = map_search(raw)

    assert [p.id for p in products] == [good["retailerProductId"]]
