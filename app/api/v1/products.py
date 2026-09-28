"""`GET /api/v1/products` — product search (RF-1)."""

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Query, Security

from app.core.dependencies import get_product_service
from app.core.security import require_api_key
from app.models.product import ProductQuery, ProductSearchResponse

# Every /api/v1 endpoint requires X-API-Key; everything outside the router
# (/health, /docs, /openapi.json) stays public (spec 004 RF-1, RF-6, plan-D2).
router = APIRouter(prefix="/api/v1", tags=["products"], dependencies=[Security(require_api_key)])


class ProductServiceLike(Protocol):
    async def search(self, query: ProductQuery) -> ProductSearchResponse: ...


@router.get("/products", response_model=ProductSearchResponse)
async def search_products(
    query: Annotated[ProductQuery, Query()],
    service: Annotated[ProductServiceLike, Depends(get_product_service)],
) -> ProductSearchResponse:
    """Search Alcampo for `query.term` (validated in the route signature, RF-2)."""
    return await service.search(query)
