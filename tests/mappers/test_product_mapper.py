import json
from pathlib import Path

import pytest

from app.mappers.product_mapper import UNIT_SUFFIXES, format_unit_price, map_product
from app.models.alcampo import AlcampoMoney, AlcampoProduct, AlcampoUnitPrice

FIXTURES = Path(__file__).parents[1] / "fixtures"


def first_raw_product() -> dict:
    raw = json.loads((FIXTURES / "alcampo_search_leche.json").read_text(encoding="utf-8"))
    return raw["productGroups"][0]["decoratedProducts"][0]


def unit_price(amount: str | None, unit_name: str | None) -> AlcampoUnitPrice:
    price = AlcampoMoney(amount=amount) if amount is not None else None
    return AlcampoUnitPrice(price=price, unitName=unit_name)


@pytest.mark.parametrize(
    ("amount", "unit_name", "expected"),
    [
        ("0.88", "PER_LITRE", "0.88 €/L"),
        ("0.80", "PER_LITRE", "0.80 €/L"),
        # Seen live on 2026-10-05, not the guessed PER_KG (spec 013 RF-1).
        ("3.50", "PER_1KG", "3.50 €/kg"),
    ],
)
def test_format_unit_price_known_units(amount: str, unit_name: str, expected: str) -> None:
    assert format_unit_price(unit_price(amount, unit_name)) == expected


def test_the_unit_table_holds_only_units_seen_live() -> None:
    # Each one with a real fixture: leche (PER_LITRE), arroz (PER_1KG). Adding a
    # unit means adding its evidence too (spec 013 RF-2, plan-D3).
    assert UNIT_SUFFIXES.keys() == {"PER_LITRE", "PER_1KG"}


@pytest.mark.parametrize("unit_name", ["PER_KG", "PER_EACH", "PER_METER"])
def test_guessed_units_never_seen_are_unknown(unit_name: str) -> None:
    # Guessed in spec 001 (spec-D8); PER_KG proved wrong (spec 013 spec-D1).
    assert format_unit_price(unit_price("1.00", unit_name)) is None


def test_format_unit_price_unknown_unit_returns_none() -> None:
    assert format_unit_price(unit_price("1.00", "PER_DOSE")) is None


def test_format_unit_price_none_returns_none() -> None:
    assert format_unit_price(None) is None


def test_format_unit_price_without_price_returns_none() -> None:
    assert format_unit_price(AlcampoUnitPrice(price=None, unitName="PER_LITRE")) is None


def test_map_product_from_real_fixture() -> None:
    raw = AlcampoProduct.model_validate(first_raw_product())

    product = map_product(raw)

    assert product.id == "54180"
    assert product.name == "AUCHAN Leche semidesnatada de vaca 6 x 1l Producto Alcampo."
    assert product.price == 5.28
    assert product.price_format == "0.88 €/L"
    assert product.category == "Leche semidesnatada"
    assert product.image_url == (
        "https://www.compraonline.alcampo.es/images-v3/"
        "37ea0506-72ec-4543-93c8-a77bb916ec12/1aec1514-3cce-46d9-8133-20fdebbdb2cc/300x300.jpg"
    )
