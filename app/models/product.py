"""Pydantic schemas for the public API (`app/api/v1`)."""

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_serializer

# Up to 100 characters, as in Mercadona (spec 009 RF-3); Alcampo itself only
# receives the first 50 of the normalized term, as its own web client does.
SearchTerm = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
]

# Deepest page served: Alcampo pages by cursor, so page N costs walking from the
# last known page (spec 009 RF-8, spec-D2). Mirrors Mercadona's effective limit
# (1000 results with 50 per page).
MAX_PAGE = 20
MAX_PAGE_SIZE = 100

# Exactly 5 ASCII digits (spec 007 RF-1, plan-D7). `[0-9]`, not `\d`: `\d` also
# matches other Unicode digits, such as fullwidth ones (U+FF10..U+FF19).
PostalCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{5}$")]


class ProductQuery(BaseModel):
    """Query parameters for `GET /api/v1/products`."""

    postal_code: PostalCode
    term: SearchTerm
    # Same parameters and defaults as Mercadona (spec 009 RF-1, RF-2).
    page: int = Field(default=1, ge=1, le=MAX_PAGE)
    page_size: int = Field(default=50, ge=1, le=MAX_PAGE_SIZE)


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
