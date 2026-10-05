"""Maps raw Alcampo payloads (`app.models.alcampo`) to public API schemas (`app.models.product`).

Unit table (spec-D8): only PER_LITRE is verified live (Fase 0). PER_KG, PER_EACH
and PER_METER are inferred from the web bundle's translation keys
(`fop.price.per.each`, `.meter`, ...) and NOT observed in a real response.
Any other unit name degrades to `price_format: None` (RF-8) instead of failing.
"""

import logging

from pydantic import JsonValue, ValidationError

from app.exceptions import UpstreamFormatError
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
        image_url=raw.image.src,
        category=raw.category_path[-1],
    )


def map_search(raw: AlcampoSearchResponse) -> list[Product]:
    """Map a search envelope to the list of products returned by the API.

    Malformed products are discarded instead of failing the whole search
    (RF-9, spec-D7). Products repeated across groups are deduplicated by
    `retailerProductId`, keeping the first occurrence (RF-4, spec-D7).

    Discards are logged once per search (spec 003 RF-14, plan-D9), naming the
    fields that failed, never their values (spec 011 RF-5, plan-D3). Discarding
    every product raises `UpstreamFormatError` instead: an empty list would look
    like "no results" and be cached, while it points to Alcampo changing its
    JSON format (spec 011 RF-1). The 502 handler logs that single ERROR (plan-D2).
    """
    products: list[Product] = []
    seen_ids: set[str] = set()
    discarded_ids: list[str | None] = []
    failed_fields: set[str] = set()
    unknown_units: set[str] = set()

    for group in raw.product_groups:
        for raw_product in group.decorated_products:
            try:
                product_data = AlcampoProduct.model_validate(raw_product)
            except ValidationError as exc:
                discarded_ids.append(_raw_product_id(raw_product))
                failed_fields.update(_failed_fields(exc))
                continue

            if product_data.retailer_product_id in seen_ids:
                continue
            seen_ids.add(product_data.retailer_product_id)
            products.append(map_product(product_data))
            unit_name = product_data.unit_price.unit_name if product_data.unit_price else None
            if unit_name is not None and unit_name not in UNIT_SUFFIXES:
                unknown_units.add(unit_name)

    if discarded_ids and not products:
        raise UpstreamFormatError(
            f"all products malformed discarded={len(discarded_ids)} "
            f"fields={sorted(failed_fields)!r}"
        )
    if discarded_ids:
        logger.warning(
            "discarded malformed products discarded=%d kept=%d ids=%r fields=%r",
            len(discarded_ids),
            len(products),
            discarded_ids,
            sorted(failed_fields),
        )
    if unknown_units:
        # Only PER_LITRE is verified live: real traffic tells which others exist
        # (spec 011 RF-6, spec-D3). They still map to `price_format: None`.
        logger.warning("unknown price units units=%r", sorted(unknown_units))
    return products


def _failed_fields(exc: ValidationError) -> set[str]:
    """`path:type` of each error, with Alcampo's field names; never the input (plan-D3)."""
    return {
        ".".join(str(part) for part in error["loc"]) + ":" + error["type"] for error in exc.errors()
    }


def _raw_product_id(raw_product: JsonValue) -> str | None:
    """Best-effort `retailerProductId` of a product that failed validation."""
    if isinstance(raw_product, dict):
        product_id = raw_product.get("retailerProductId")
        if isinstance(product_id, str):
            return product_id
    return None
