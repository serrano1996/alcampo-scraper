import json
import logging
from pathlib import Path

import pytest

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


def mapper_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "app.mappers.product_mapper"]


def envelope(*products: dict) -> AlcampoSearchResponse:
    return AlcampoSearchResponse.model_validate(
        {"productGroups": [{"decoratedProducts": list(products)}]}
    )


def test_partial_discard_logs_one_warning_with_count_and_ids(
    caplog: pytest.LogCaptureFixture,
) -> None:
    good = first_raw_product()
    broken = {**first_raw_product(), "retailerProductId": "1", "name": ""}
    other = {**first_raw_product(), "retailerProductId": "2"}

    map_search(envelope(good, broken, other))

    [record] = mapper_records(caplog)
    assert record.levelno == logging.WARNING
    assert "discarded=1" in record.getMessage()
    assert "'1'" in record.getMessage()


def test_discarding_every_product_is_an_error(caplog: pytest.LogCaptureFixture) -> None:
    broken_a = {**first_raw_product(), "name": ""}
    broken_b = {**first_raw_product(), "retailerProductId": "2", "price": {"amount": "abc"}}

    assert map_search(envelope(broken_a, broken_b)) == []

    [record] = mapper_records(caplog)
    assert record.levelno == logging.ERROR
    assert "discarded=2" in record.getMessage()


def test_duplicates_are_not_counted_as_discarded(caplog: pytest.LogCaptureFixture) -> None:
    product = first_raw_product()

    map_search(envelope(product, product))

    assert mapper_records(caplog) == []


def test_clean_searches_log_nothing(caplog: pytest.LogCaptureFixture) -> None:
    map_search(AlcampoSearchResponse.model_validate(load_fixture("alcampo_search_leche.json")))
    map_search(AlcampoSearchResponse.model_validate(load_fixture("alcampo_search_no_results.json")))

    assert mapper_records(caplog) == []
