"""Pydantic schemas for the public API (`app/api/v1`)."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_serializer

SearchTerm = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
]
PostalCode = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ProductQuery(BaseModel):
    """Query parameters for `GET /api/v1/products`."""

    postal_code: PostalCode
    term: SearchTerm


class Product(BaseModel):
    """A single product as returned to the API consumer."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    price: float
    price_format: str | None
    image_url: str | None
    category: str | None


class SearchMetadata(BaseModel):
    """Metadata describing how a search was performed."""

    model_config = ConfigDict(frozen=True)

    postal_code: str
    term: str
    warehouse: str
    strategy_used: str
    scraped_at: datetime
    total_results: int

    @field_serializer("scraped_at")
    def serialize_scraped_at(self, value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")


class ProductSearchResponse(BaseModel):
    """Response body of `GET /api/v1/products`."""

    model_config = ConfigDict(frozen=True)

    search: SearchMetadata
    products: list[Product] = Field(default_factory=list)
