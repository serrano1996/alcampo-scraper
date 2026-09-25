"""Orchestrates cache, scraper and mapper for `GET /api/v1/products` (RF-1)."""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from app.core.config import Settings
from app.mappers.product_mapper import map_search
from app.models.alcampo import AlcampoSearchResponse
from app.models.product import ProductQuery, ProductSearchResponse, SearchMetadata
from app.scrapers.alcampo_search import DEFAULT_WAREHOUSE
from app.services.search_cache import SearchCacheRepository

Clock = Callable[[], datetime]


class SearchScraper(Protocol):
    async def search(self, term: str) -> AlcampoSearchResponse: ...


def _default_clock() -> datetime:
    return datetime.now(UTC)


class ProductService:
    """Business logic behind the search endpoint: cache first, then Alcampo."""

    def __init__(
        self,
        *,
        scraper: SearchScraper,
        cache: SearchCacheRepository,
        settings: Settings,
        clock: Clock = _default_clock,
    ) -> None:
        self._scraper = scraper
        self._cache = cache
        self._settings = settings
        self._clock = clock

    async def search(self, query: ProductQuery) -> ProductSearchResponse:
        """Search Alcampo (or the cache) for `query.term` (RF-1, RF-5, RF-10, RF-11, RF-12)."""
        cached = await self._cache.get(warehouse=DEFAULT_WAREHOUSE, term=query.term)
        if cached is not None:
            return cached.model_copy(
                update={
                    "search": cached.search.model_copy(
                        update={"postal_code": query.postal_code, "term": query.term}
                    )
                }
            )

        raw = await self._scraper.search(query.term)
        products = map_search(raw)
        response = ProductSearchResponse(
            search=SearchMetadata(
                postal_code=query.postal_code,
                term=query.term,
                warehouse=DEFAULT_WAREHOUSE,
                strategy_used="api",
                scraped_at=self._clock(),
                total_results=len(products),
            ),
            products=products,
        )
        await self._cache.set(
            warehouse=DEFAULT_WAREHOUSE,
            term=query.term,
            response=response,
            ttl_seconds=self._settings.cache_ttl_seconds,
        )
        return response
