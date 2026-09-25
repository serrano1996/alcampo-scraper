"""Maps raw Alcampo payloads (`app.models.alcampo`) to public API schemas (`app.models.product`).

Unit table (spec-D8): only PER_LITRE is verified live (Fase 0). PER_KG, PER_EACH
and PER_METER are inferred from the web bundle's translation keys
(`fop.price.per.each`, `.meter`, ...) and NOT observed in a real response.
Any other unit name degrades to `price_format: None` (RF-8) instead of failing.
"""

import logging

from pydantic import JsonValue, ValidationError

from app.models.alcampo import AlcampoProduct, AlcampoSearchResponse, AlcampoUnitPrice
from app.models.product import Product

logger = logging.getLogger(__name__)

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


def map_search(raw: AlcampoSearchResponse) -> list[Product]:
    """Map a search envelope to the list of products returned by the API.

    Malformed products are discarded instead of failing the whole search
    (RF-9, spec-D7). Products repeated across groups are deduplicated by
    `retailerProductId`, keeping the first occurrence (RF-4, spec-D7).

    Discards are logged once per search (spec 003 RF-14, plan-D9). Discarding
    every product is an ERROR (RF-15): it looks like "no results" to the
    consumer but points to Alcampo changing its JSON format.
    """
    products: list[Product] = []
    seen_ids: set[str] = set()
    discarded_ids: list[str | None] = []

    for group in raw.product_groups:
        for raw_product in group.decorated_products:
            try:
                product_data = AlcampoProduct.model_validate(raw_product)
            except ValidationError:
                discarded_ids.append(_raw_product_id(raw_product))
                continue

            if product_data.retailer_product_id in seen_ids:
                continue
            seen_ids.add(product_data.retailer_product_id)
            products.append(map_product(product_data))

    if discarded_ids:
        level = logging.ERROR if not products else logging.WARNING
        logger.log(
            level,
            "discarded malformed products discarded=%d kept=%d ids=%r",
            len(discarded_ids),
            len(products),
            discarded_ids,
        )
    return products


def _raw_product_id(raw_product: JsonValue) -> str | None:
    """Best-effort `retailerProductId` of a product that failed validation."""
    if isinstance(raw_product, dict):
        product_id = raw_product.get("retailerProductId")
        if isinstance(product_id, str):
            return product_id
    return None
