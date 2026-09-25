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
