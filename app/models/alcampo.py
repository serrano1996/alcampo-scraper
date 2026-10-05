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

    src: str = Field(min_length=1)


class AlcampoProduct(BaseModel):
    """A single product as returned inside `productGroups[*].decoratedProducts[]`."""

    model_config = ConfigDict(extra="ignore")

    retailer_product_id: str = Field(alias="retailerProductId", min_length=1)
    name: str = Field(min_length=1)
    price: AlcampoMoney
    unit_price: AlcampoUnitPrice | None = Field(default=None, alias="unitPrice")
    # Required, like Mercadona's non-null `image_url` and `category`: a product
    # without them is discarded as malformed (spec 009 RF-11, spec-D4).
    image: AlcampoImage
    category_path: list[str] = Field(alias="categoryPath", min_length=1)


class AlcampoProductGroup(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Validated per-item by the mapper (see plan-D1): a single malformed product
    # must not fail the whole search response.
    decorated_products: list[JsonValue] = Field(default_factory=list, alias="decoratedProducts")


class AlcampoSearchMetadata(BaseModel):
    """Paging cursor. The last page arrives as `metadata: {}` (spec 009 §10)."""

    model_config = ConfigDict(extra="ignore")

    # Bound to the session that received it (spec 009 plan-D1).
    next_page_token: str | None = Field(default=None, alias="nextPageToken", min_length=1)


class AlcampoCategoryCount(BaseModel):
    """A category of the results; top-level counts add up to the estimated total."""

    model_config = ConfigDict(extra="ignore")

    product_count: int | None = Field(default=None, alias="productCount", ge=0)


class AlcampoAdditionalPageInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    categories: list[AlcampoCategoryCount] = Field(default_factory=list)


class AlcampoSearchResponse(BaseModel):
    """Envelope returned by `GET /api/webproductpagews/v6/product-pages/search`."""

    model_config = ConfigDict(extra="ignore")

    product_groups: list[AlcampoProductGroup] = Field(alias="productGroups")
    # Optional: pagination (spec 009 RF-4, RF-7) must not break the single page.
    metadata: AlcampoSearchMetadata | None = None
    additional_page_info: AlcampoAdditionalPageInfo | None = Field(
        default=None, alias="additionalPageInfo"
    )


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
