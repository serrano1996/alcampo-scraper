"""Maps raw Alcampo payloads (`app.models.alcampo`) to public API schemas (`app.models.product`).

Unit table (spec-D8): only PER_LITRE is verified live (Fase 0). PER_KG, PER_EACH
and PER_METER are inferred from the web bundle's translation keys
(`fop.price.per.each`, `.meter`, ...) and NOT observed in a real response.
Any other unit name degrades to `price_format: None` (RF-8) instead of failing.
"""

from app.models.alcampo import AlcampoProduct, AlcampoUnitPrice
from app.models.product import Product

UNIT_SUFFIXES: dict[str, str] = {
    "PER_LITRE": "L",
    "PER_KG": "kg",
    "PER_EACH": "ud",
    "PER_METER": "m",
}


def format_unit_price(unit_price: AlcampoUnitPrice | None) -> str | None:
    """Build the `"<amount> €/<unit>"` string for `Product.price_format` (RF-7, RF-8)."""
    if unit_price is None or unit_price.price is None or unit_price.unit_name is None:
        return None

    suffix = UNIT_SUFFIXES.get(unit_price.unit_name)
    if suffix is None:
        return None

    return f"{unit_price.price.amount} €/{suffix}"


def map_product(raw: AlcampoProduct) -> Product:
    """Map a validated raw Alcampo product to the public API schema (RF-6)."""
    return Product(
        id=raw.retailer_product_id,
        name=raw.name,
        price=float(raw.price.amount),
        price_format=format_unit_price(raw.unit_price),
        image_url=raw.image.src if raw.image else None,
        category=raw.category_path[-1] if raw.category_path else None,
    )
