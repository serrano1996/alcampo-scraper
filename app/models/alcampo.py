"""Raw payload schemas for Alcampo's internal search endpoint (webproductpagews v6).

Each model uses `extra="ignore"`: the real response carries dozens of fields per
product that this scraper does not need (promotions, ratings, basket state...).
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

DecimalString = Annotated[str, StringConstraints(pattern=r"^[0-9]+(\.[0-9]+)?$")]


class AlcampoMoney(BaseModel):
    model_config = ConfigDict(extra="ignore")

    amount: DecimalString


class AlcampoUnitPrice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    price: AlcampoMoney | None = None
    unit_name: str | None = Field(default=None, alias="unitName")


class AlcampoImage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    src: str | None = None


class AlcampoProduct(BaseModel):
    """A single product as returned inside `productGroups[*].decoratedProducts[]`."""

    model_config = ConfigDict(extra="ignore")

    retailer_product_id: str = Field(alias="retailerProductId", min_length=1)
    name: str = Field(min_length=1)
    price: AlcampoMoney
    unit_price: AlcampoUnitPrice | None = Field(default=None, alias="unitPrice")
    image: AlcampoImage | None = None
    category_path: list[str] = Field(default_factory=list, alias="categoryPath")


class AlcampoProductGroup(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Validated per-item by the mapper (see plan-D1): a single malformed product
    # must not fail the whole search response.
    decorated_products: list[JsonValue] = Field(default_factory=list, alias="decoratedProducts")


class AlcampoSearchResponse(BaseModel):
    """Envelope returned by `GET /api/webproductpagews/v6/product-pages/search`."""

    model_config = ConfigDict(extra="ignore")

    product_groups: list[AlcampoProductGroup] = Field(alias="productGroups")


# --- Postal code -> region chain (spec 007, Fase 0 §3, verified live in plan §2) ---


class AlcampoArea(BaseModel):
    """Step 1: one geocoded area for a postal code (a Google Place ID)."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)


class AlcampoAreaDetails(BaseModel):
    """Step 2: coordinates and address of an area, needed by steps 3 and 4."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    latitude: float
    longitude: float
    postal_code: str = Field(alias="postalCode", min_length=1)
    formatted_address: str = Field(alias="formattedAddress", min_length=1)


class AlcampoDeliverability(BaseModel):
    """Step 3: `DELIVERABLE`, or `NOT_DELIVERABLE` (seen for Ceuta and Melilla)."""

    model_config = ConfigDict(extra="ignore")

    deliverability: str = Field(min_length=1)


class AlcampoDeliveryAddress(BaseModel):
    """Step 5: the region that serves a delivery destination."""

    model_config = ConfigDict(extra="ignore")

    resolved_region_id: str = Field(alias="resolvedRegionId", min_length=1)


class AlcampoCartProposition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cart_proposition_id: str = Field(alias="cartPropositionId", min_length=1)


class AlcampoSessionProposition(BaseModel):
    """Step 6: previews a region change. It does not apply it; step 7 does."""

    model_config = ConfigDict(extra="ignore")

    origin: AlcampoCartProposition = Field(alias="originCartProposition")
    destination: AlcampoCartProposition = Field(alias="destinationCartProposition")


class AlcampoActiveSession(BaseModel):
    """Step 7: confirms the region change. No `retailerRegionId` here: that is in the HTML."""

    model_config = ConfigDict(extra="ignore")

    region_id: str = Field(alias="regionId", min_length=1)
