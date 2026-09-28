"""Orchestrates cache, scraper and mapper for `GET /api/v1/products` (RF-1)."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from app.core.config import Settings
from app.exceptions import CooldownActiveError, UpstreamBlockedError
from app.mappers.product_mapper import map_search
from app.models.alcampo import AlcampoSearchResponse
from app.models.product import ProductQuery, ProductSearchResponse, SearchMetadata
from app.scrapers.alcampo_search import DEFAULT_WAREHOUSE
from app.services.search_cache import SearchCacheRepository, normalize_term
from app.services.waf_cooldown import WafCooldownRepository

Clock = Callable[[], datetime]

logger = logging.getLogger(__name__)


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
        cooldown: WafCooldownRepository,
        settings: Settings,
        clock: Clock = _default_clock,
    ) -> None:
        self._scraper = scraper
        self._cache = cache
        self._cooldown = cooldown
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

        # Checked after the cache on purpose (plan-D3): cached searches keep
        # working during a cooldown (spec 002 RF-17); misses fail fast without
        # touching Alcampo (RF-16).
        if await self._cooldown.is_active():
            raise CooldownActiveError("WAF cooldown active")

        try:
            # The normalized term, so the request does not depend on the client's
            # spelling (spec 008 RF-2, plan-D1); the response keeps query.term.
            raw = await self._scraper.search(normalize_term(query.term))
        except UpstreamBlockedError:
            # The WAF blocked the egress IP: stop hitting Alcampo for a while
            # (spec 002 RF-15). Plain upstream errors do not start a cooldown.
            # Logged here, the only layer that knows the cooldown (spec 003 RF-11).
            cooldown_s = self._settings.waf_cooldown_seconds
            if cooldown_s > 0:
                logger.error("egress IP blocked by Alcampo WAF, cooldown_s=%d", cooldown_s)
            else:
                logger.error("egress IP blocked by Alcampo WAF, cooldown disabled")
            await self._cooldown.activate(ttl_seconds=cooldown_s)
            raise
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
