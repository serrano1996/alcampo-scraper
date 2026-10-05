"""Orchestrates region, cache, scraper and mapper for `GET /api/v1/products` (RF-1).

Order of a search (spec 007):
1. Region of the postal code (`RegionService`: almost always from cache; 404 if
   Alcampo does not serve it).
2. Cache by region's `retailerRegionId`, sent term, page and page size (spec 009).
3. On a miss, once per group of identical searches: WAF cooldown check, the
   region's confirmed session (`RegionSessions`), the page walk, cache writes.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol, TypeVar

from app.core.config import Settings
from app.exceptions import CooldownActiveError, UpstreamBlockedError, UpstreamUnavailableError
from app.mappers.product_mapper import map_search
from app.models.alcampo import AlcampoSearchResponse
from app.models.product import ProductQuery, ProductSearchResponse, SearchMetadata
from app.scrapers.alcampo_search import MAX_SENT_TERM_LENGTH
from app.services.in_flight import InFlightSearches
from app.services.outbound import TrafficLog
from app.services.pagination import CursorSession, PageWalker, SearchScraper, page_totals
from app.services.region_repository import Region
from app.services.search_cache import SearchCacheRepository, normalize_term
from app.services.waf_cooldown import WafCooldownRepository

Clock = Callable[[], datetime]
T = TypeVar("T")

logger = logging.getLogger(__name__)


class RegionFinder(Protocol):
    async def region_for(self, postal_code: str) -> Region: ...


class SessionProvider(Protocol):
    async def get(self, region: Region) -> CursorSession: ...


def _default_clock() -> datetime:
    return datetime.now(UTC)


class ProductService:
    """Business logic behind the search endpoint: region, cache, then Alcampo."""

    def __init__(
        self,
        *,
        scraper: SearchScraper,
        cache: SearchCacheRepository,
        cooldown: WafCooldownRepository,
        in_flight: InFlightSearches[ProductSearchResponse],
        regions: RegionFinder,
        sessions: SessionProvider,
        traffic: TrafficLog,
        settings: Settings,
        clock: Clock = _default_clock,
    ) -> None:
        self._scraper = scraper
        self._cache = cache
        self._cooldown = cooldown
        self._in_flight = in_flight
        self._regions = regions
        self._sessions = sessions
        self._traffic = traffic
        self._settings = settings
        self._clock = clock
        # Each page with its own time budget: a walk spaced by the outbound gate
        # (~1 s per request) does not fit in one (spec 009 plan-D2).
        self._walker = PageWalker(
            scraper, bound=lambda call: self._within_timeout(call, "search timeout")
        )

    async def search(self, query: ProductQuery) -> ProductSearchResponse:
        """Search Alcampo (or the cache) for `query.term` in the region of `query.postal_code`."""
        try:
            region = await self._regions.region_for(query.postal_code)
        except UpstreamBlockedError:
            # Resolving a new postal code is traffic to Alcampo too (spec 007 RF-6).
            await self._on_waf_block()
            raise
        warehouse = region.retailer_region_id
        term = sent_term(query.term)

        cached = await self._cache.get(
            warehouse=warehouse, term=term, page=query.page, page_size=query.page_size
        )
        if cached is not None:
            logger.info("search served source=hit")
            return _for_query(cached, query)

        # Simultaneous identical searches share one fetch (spec 008 RF-1). The
        # key is normalized, so `leche` and `Leche` group too (RF-2), and carries
        # the region, so two regions never share one (spec 007 RF-12).
        key = f"{warehouse}:{term}:{query.page}:{query.page_size}"
        response, shared = await self._in_flight.run(key, lambda: self._fetch(query, region, term))
        # Origin of every search, to measure how much the cache protects (RF-11).
        logger.info("search served source=%s", "shared" if shared else "miss")
        return _for_query(response, query)

    async def _fetch(self, query: ProductQuery, region: Region, term: str) -> ProductSearchResponse:
        """Cooldown check, the region's session, Alcampo and cache write, once per group.

        Runs in a task started by the first caller of the group, so its log
        lines carry that caller's request id.
        """
        # Checked after the cache on purpose (plan-D3): cached searches keep
        # working during a cooldown (spec 002 RF-17); misses fail fast without
        # touching Alcampo (RF-16).
        if await self._cooldown.is_active():
            raise CooldownActiveError("WAF cooldown active")

        try:
            # Renewing the session also talks to Alcampo: same budget, same WAF rules.
            session = await self._within_timeout(
                self._sessions.get(region), "region session timeout"
            )

            async def keep(page: int, raw: AlcampoSearchResponse) -> None:
                # Pages walked through were paid for: cached too (spec 009 RF-9).
                await self._store(self._response(query, region, raw, page), region, term, page)

            # The normalized term, so the request does not depend on the client's
            # spelling (spec 008 RF-2, plan-D1); the response keeps query.term.
            raw = await self._walker.walk(
                session, term, page=query.page, page_size=query.page_size, on_passed=keep
            )
        except UpstreamBlockedError:
            await self._on_waf_block()
            raise
        response = self._response(query, region, raw, query.page)
        await self._store(response, region, term, query.page)
        return response

    def _response(
        self, query: ProductQuery, region: Region, raw: AlcampoSearchResponse, page: int
    ) -> ProductSearchResponse:
        products = map_search(raw)
        totals = page_totals(raw, page=page, page_size=query.page_size, on_page=len(products))
        return ProductSearchResponse(
            search=SearchMetadata(
                postal_code=query.postal_code,
                term=query.term,
                # The region's real id (spec 007 RF-13): "5" is Vaguada, "32" Telde...
                warehouse=region.retailer_region_id,
                strategy_used="api",
                scraped_at=self._clock(),
                # Estimated, exact on the last page (spec 009 RF-4, RF-5).
                total_results=totals.total_results,
                page=page,
                page_size=query.page_size,
                total_pages=totals.total_pages,
            ),
            products=products,
        )

    async def _store(
        self, response: ProductSearchResponse, region: Region, term: str, page: int
    ) -> None:
        await self._cache.set(
            warehouse=region.retailer_region_id,
            term=term,
            page=page,
            page_size=response.search.page_size,
            response=response,
            ttl_seconds=self._settings.cache_ttl_seconds,
        )

    async def _on_waf_block(self) -> None:
        """The WAF blocked the egress IP: stop hitting Alcampo for a while (spec 002 RF-15).

        Plain upstream errors do not start a cooldown. Logged here, the only
        layer that knows the cooldown (spec 003 RF-11), with the duration
        actually applied, which grows on repeated challenges (spec 008 RF-8, RF-9).
        """
        cooldown_s = await self._cooldown.activate(
            base_seconds=self._settings.waf_cooldown_seconds,
            max_seconds=self._settings.waf_cooldown_max_seconds,
        )
        # What this instance had sent lately: the data to tune the outbound limits
        # with, instead of provoking more blocks (spec 010 RF-6).
        recent = self._traffic.summary()
        if cooldown_s > 0:
            logger.error(
                "egress IP blocked by Alcampo WAF, cooldown_s=%d recent_traffic=%s",
                cooldown_s,
                recent,
            )
        else:
            logger.error(
                "egress IP blocked by Alcampo WAF, cooldown disabled recent_traffic=%s", recent
            )

    async def _within_timeout(self, call: Awaitable[T], reason: str) -> T:
        """Await `call` with a total budget: attempts and waits included.

        httpx's timeout bounds each attempt, not their sum (~30 s worst case), so
        the whole call is capped here (spec 008 RF-7, plan-D6). The in-flight
        request is cancelled. The 502 handler logs the ERROR with this reason.
        """
        try:
            async with asyncio.timeout(self._settings.search_timeout_seconds):
                return await call
        except TimeoutError as exc:
            raise UpstreamUnavailableError(reason) from exc


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


def sent_term(term: str) -> str:
    """What Alcampo receives: normalized, at most 50 characters (spec 009 RF-3, plan-D5)."""
    return normalize_term(term)[:MAX_SENT_TERM_LENGTH].strip()
