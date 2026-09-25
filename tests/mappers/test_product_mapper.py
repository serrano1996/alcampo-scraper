import pytest

from app.mappers.product_mapper import format_unit_price
from app.models.alcampo import AlcampoMoney, AlcampoUnitPrice


def unit_price(amount: str | None, unit_name: str | None) -> AlcampoUnitPrice:
    price = AlcampoMoney(amount=amount) if amount is not None else None
    return AlcampoUnitPrice(price=price, unitName=unit_name)


@pytest.mark.parametrize(
    ("amount", "unit_name", "expected"),
    [
        ("0.88", "PER_LITRE", "0.88 €/L"),
        ("0.80", "PER_LITRE", "0.80 €/L"),
        ("3.50", "PER_KG", "3.50 €/kg"),
        ("1.20", "PER_EACH", "1.20 €/ud"),
        ("2.00", "PER_METER", "2.00 €/m"),
    ],
)
def test_format_unit_price_known_units(amount: str, unit_name: str, expected: str) -> None:
    assert format_unit_price(unit_price(amount, unit_name)) == expected


def test_format_unit_price_unknown_unit_returns_none() -> None:
    assert format_unit_price(unit_price("1.00", "PER_DOSE")) is None


def test_format_unit_price_none_returns_none() -> None:
    assert format_unit_price(None) is None


def test_format_unit_price_without_price_returns_none() -> None:
    assert format_unit_price(AlcampoUnitPrice(price=None, unitName="PER_LITRE")) is None
