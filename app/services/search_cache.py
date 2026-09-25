"""Redis-backed cache for search responses.

Keyed by warehouse (region), not by postal code (plan-D4): all postal codes
resolving to the same region share a cache entry. That resolution arrives in
spec 007; today every request uses `DEFAULT_WAREHOUSE`.
"""

from redis.asyncio import Redis

from app.models.product import ProductSearchResponse


def _cache_key(*, warehouse: str, term: str) -> str:
    return f"search:{warehouse}:{term}"


class SearchCacheRepository:
    """Get/set `ProductSearchResponse` entries in Redis (RF-11, RF-12)."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(self, *, warehouse: str, term: str) -> ProductSearchResponse | None:
        """Return the cached response, or `None` on a miss or corrupted value (plan-D8)."""
        raw = await self._redis.get(_cache_key(warehouse=warehouse, term=term))
        if raw is None:
            return None
        try:
            return ProductSearchResponse.model_validate_json(raw)
        except ValueError:
            return None

    async def set(
        self,
        *,
        warehouse: str,
        term: str,
        response: ProductSearchResponse,
        ttl_seconds: int,
    ) -> None:
        await self._redis.set(
            _cache_key(warehouse=warehouse, term=term),
            response.model_dump_json(),
            ex=ttl_seconds,
        )
