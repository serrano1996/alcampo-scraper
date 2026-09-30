"""Redis-backed cache for search responses.

Keyed by warehouse (region), not by postal code (plan-D4): all postal codes
resolving to the same region share a cache entry, and two regions never mix
(spec 007 RF-12). The warehouse is the region's `retailerRegionId`.
"""

import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.models.product import ProductSearchResponse

logger = logging.getLogger(__name__)


def normalize_term(term: str) -> str:
    """Canonical form of a search term: casefolded, whitespace runs collapsed to one space.

    Verified live on 2026-09-28 (spec 008 plan §2): Alcampo returns identical
    results for `leche`/`Leche`/`LECHE` and for `leche entera`/`leche   entera`,
    so variants can share one cache entry and one upstream request (RF-2, plan-D1).
    """
    return " ".join(term.split()).casefold()


def _cache_key(*, warehouse: str, term: str) -> str:
    return f"search:{warehouse}:{normalize_term(term)}"


class SearchCacheRepository:
    """Get/set `ProductSearchResponse` entries in Redis (RF-11, RF-12)."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(self, *, warehouse: str, term: str) -> ProductSearchResponse | None:
        """Return the cached response, or `None` on a miss or corrupted value (plan-D8)."""
        key = _cache_key(warehouse=warehouse, term=term)
        try:
            raw = await self._redis.get(key)
        except RedisError:
            # Degrade to Alcampo instead of a 500 (spec 007 RF-15, plan-D10).
            logger.warning("redis unavailable op=cache.get, degraded")
            return None
        if raw is None:
            return None
        try:
            return ProductSearchResponse.model_validate_json(raw)
        except ValueError:
            # The key contains the client's term: logged with %r (spec 003 RF-16, RF-18).
            logger.warning("corrupted cache entry treated as a miss key=%r", key)
            return None

    async def set(
        self,
        *,
        warehouse: str,
        term: str,
        response: ProductSearchResponse,
        ttl_seconds: int,
    ) -> None:
        try:
            await self._redis.set(
                _cache_key(warehouse=warehouse, term=term),
                response.model_dump_json(),
                ex=ttl_seconds,
            )
        except RedisError:
            # The response is still returned; it just is not cached (spec 007 RF-15).
            logger.warning("redis unavailable op=cache.set, degraded")
