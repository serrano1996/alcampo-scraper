"""Orchestrates cache, scraper and mapper for `GET /api/v1/products` (RF-1)."""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from app.core.config import Settings
from app.exceptions import CooldownActiveError, UpstreamBlockedError, UpstreamUnavailableError
from app.mappers.product_mapper import map_search
from app.models.alcampo import AlcampoSearchResponse
from app.models.product import ProductQuery, ProductSearchResponse, SearchMetadata
from app.scrapers.alcampo_search import DEFAULT_WAREHOUSE
from app.services.in_flight import InFlightSearches
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
        in_flight: InFlightSearches[ProductSearchResponse],
        settings: Settings,
        clock: Clock = _default_clock,
    ) -> None:
        self._scraper = scraper
        self._cache = cache
        self._cooldown = cooldown
        self._in_flight = in_flight
        self._settings = settings
        self._clock = clock

    async def search(self, query: ProductQuery) -> ProductSearchResponse:
        """Search Alcampo (or the cache) for `query.term` (RF-1, RF-5, RF-10, RF-11, RF-12)."""
        cached = await self._cache.get(warehouse=DEFAULT_WAREHOUSE, term=query.term)
        if cached is not None:
            logger.info("search served source=hit")
            return _for_query(cached, query)

        # Simultaneous identical searches share one fetch (spec 008 RF-1). The
        # key is normalized, so `leche` and `Leche` group too (RF-2).
        key = f"{DEFAULT_WAREHOUSE}:{normalize_term(query.term)}"
        response, shared = await self._in_flight.run(key, lambda: self._fetch(query))
        # Origin of every search, to measure how much the cache protects (RF-11).
        logger.info("search served source=%s", "shared" if shared else "miss")
        return _for_query(response, query)

    async def _fetch(self, query: ProductQuery) -> ProductSearchResponse:
        """Cooldown check, Alcampo and cache write: run once per group of searches.

        Runs in a task started by the first caller of the group, so its log
        lines carry that caller's request id.
        """
        # Checked after the cache on purpose (plan-D3): cached searches keep
        # working during a cooldown (spec 002 RF-17); misses fail fast without
        # touching Alcampo (RF-16).
        if await self._cooldown.is_active():
            raise CooldownActiveError("WAF cooldown active")

        try:
            # The normalized term, so the request does not depend on the client's
            # spelling (spec 008 RF-2, plan-D1); the response keeps query.term.
            raw = await self._search_within_timeout(normalize_term(query.term))
        except UpstreamBlockedError:
            # The WAF blocked the egress IP: stop hitting Alcampo for a while
            # (spec 002 RF-15). Plain upstream errors do not start a cooldown.
            # Logged here, the only layer that knows the cooldown (spec 003 RF-11),
            # with the duration actually applied, which grows on repeated
            # challenges (spec 008 RF-8, RF-9).
            cooldown_s = await self._cooldown.activate(
                base_seconds=self._settings.waf_cooldown_seconds,
                max_seconds=self._settings.waf_cooldown_max_seconds,
            )
            if cooldown_s > 0:
                logger.error("egress IP blocked by Alcampo WAF, cooldown_s=%d", cooldown_s)
            else:
                logger.error("egress IP blocked by Alcampo WAF, cooldown disabled")
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

    async def _search_within_timeout(self, term: str) -> AlcampoSearchResponse:
        """Call the scraper with a total budget: attempts and waits included.

        httpx's timeout bounds each attempt, not their sum (~30 s worst case), so
        the whole call is capped here (spec 008 RF-7, plan-D6). The in-flight
        request is cancelled. The 502 handler logs the ERROR with this reason.
        """
        try:
            async with asyncio.timeout(self._settings.search_timeout_seconds):
                return await self._scraper.search(term)
        except TimeoutError as exc:
            raise UpstreamUnavailableError("search timeout") from exc


def _for_query(response: ProductSearchResponse, query: ProductQuery) -> ProductSearchResponse:
    """The same results, labelled with this caller's postal code and term.

    Cached and shared responses were built for another request; the client
    still sees what it asked for (spec 001, spec 008 RF-2).
    """
    return response.model_copy(
        update={
            "search": response.search.model_copy(
                update={"postal_code": query.postal_code, "term": query.term}
            )
        }
    )
