import json
import logging
from pathlib import Path

import pytest

from app.exceptions import UpstreamFormatError
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


def test_discarding_every_product_is_an_upstream_format_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Spec 011 RF-1: no longer `[]` (a 200 that looks like "no results", cached).
    # The 502 handler logs the single ERROR with this reason (plan-D2).
    broken_a = {**first_raw_product(), "name": ""}
    broken_b = {**first_raw_product(), "retailerProductId": "2", "price": {"amount": "abc"}}

    with pytest.raises(UpstreamFormatError) as raised:
        map_search(envelope(broken_a, broken_b))

    assert "discarded=2" in raised.value.reason
    assert "fields=['name:string_too_short', 'price.amount:string_pattern_mismatch']" in (
        raised.value.reason
    )
    assert "abc" not in raised.value.reason
    assert mapper_records(caplog) == []


@pytest.mark.parametrize(
    "body",
    [{"productGroups": []}, {"productGroups": [{"decoratedProducts": []}, {}]}],
    ids=["no-groups", "empty-groups"],
)
def test_a_search_without_products_is_not_a_format_error(body: dict) -> None:
    # Spec 011 RF-2: real "no results" stays a 200 with an empty list.
    assert map_search(AlcampoSearchResponse.model_validate(body)) == []


def test_duplicates_are_not_counted_as_discarded(caplog: pytest.LogCaptureFixture) -> None:
    product = first_raw_product()

    map_search(envelope(product, product))

    assert mapper_records(caplog) == []


def test_clean_searches_log_nothing(caplog: pytest.LogCaptureFixture) -> None:
    map_search(AlcampoSearchResponse.model_validate(load_fixture("alcampo_search_leche.json")))
    map_search(AlcampoSearchResponse.model_validate(load_fixture("alcampo_search_no_results.json")))

    assert mapper_records(caplog) == []


# --- spec 009 RF-11: image and category are never null, as in Mercadona ------
# Replaces spec 001's test_map_product_without_{category_path,image}_returns_none_*:
# a product without them is now discarded like any malformed one (spec-D4; 0 of
# 100 real products lacked them, spec 009 §10).


@pytest.mark.parametrize(
    "change",
    [
        {"image": None},
        {"image": {"src": ""}},
        {"categoryPath": []},
    ],
    ids=["no-image", "empty-image-src", "no-category"],
)
def test_a_product_without_image_or_category_is_discarded(
    caplog: pytest.LogCaptureFixture, change: dict
) -> None:
    good = first_raw_product()
    incomplete = {**first_raw_product(), "retailerProductId": "9", **change}
    raw = AlcampoSearchResponse.model_validate(
        {"productGroups": [{"decoratedProducts": [good, incomplete]}]}
    )

    products = map_search(raw)

    assert [p.id for p in products] == [good["retailerProductId"]]
    [warning] = mapper_records(caplog)
    assert "discarded=1" in warning.getMessage()
    assert "'9'" in warning.getMessage()


def test_a_product_missing_the_image_key_is_discarded() -> None:
    incomplete = {**first_raw_product(), "retailerProductId": "9"}
    del incomplete["image"]
    # With a valid one beside it: alone, it would be a format error (spec 011 RF-1).
    raw = AlcampoSearchResponse.model_validate(
        {"productGroups": [{"decoratedProducts": [first_raw_product(), incomplete]}]}
    )

    assert [p.id for p in map_search(raw)] == ["54180"]


# --- spec 011 RF-5: which field failed, never its value -------------------------


def product(product_id: str, **changes: object) -> dict:
    """The first real product with another id and some fields replaced or removed (None)."""
    data = {**first_raw_product(), "retailerProductId": product_id}
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data


def test_the_discard_warning_names_the_failed_fields_without_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    no_image = product("2", image=None)
    numeric_price = product("3", price={"amount": 5.28, "currency": "EUR"})

    map_search(envelope(product("1"), no_image, numeric_price))

    [record] = mapper_records(caplog)
    message = record.getMessage()
    assert record.levelno == logging.WARNING
    assert "fields=['image:missing', 'price.amount:string_type']" in message
    assert "5.28" not in message  # the value received is Alcampo's data (plan-D3)


def test_a_field_failing_in_several_products_is_named_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    map_search(envelope(product("1"), product("2", image=None), product("3", image=None)))

    [record] = mapper_records(caplog)
    assert "fields=['image:missing']" in record.getMessage()


# --- spec 011 RF-6: unknown price units ----------------------------------------


def unit_price(unit_name: str) -> dict:
    return {"price": {"amount": "0.88", "currency": "EUR"}, "unitName": unit_name}


def test_an_unknown_price_unit_is_warned_once_per_search(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw = envelope(
        product("1", unitPrice=unit_price("PER_100G")),
        product("2", unitPrice=unit_price("PER_100G")),
    )

    products = map_search(raw)

    assert [p.price_format for p in products] == [None, None]
    [record] = mapper_records(caplog)
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "unknown price units units=['PER_100G']"


def test_known_price_units_are_not_warned(caplog: pytest.LogCaptureFixture) -> None:
    map_search(envelope(product("1"), product("2", unitPrice=unit_price("PER_1KG"))))

    assert mapper_records(caplog) == []
