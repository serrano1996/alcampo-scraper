"""Postal code -> region records, in Redis and in process memory (spec 007 RF-3, RF-4).

Resolving a postal code costs ~8 requests to Alcampo, one of them the most
WAF-sensitive (creating a delivery destination), so every answer is kept:

- `postal-code-region:{cp}` -> region id, or `NOT_SERVED` (1 h: cheap to repeat).
- `region:{region_id}` -> retailer region id and the destination created when it
  was resolved, so its session can be renewed without creating another one
  (RF-11 as amended after the live check, plan §2).

A per-process memory (L1) sits in front of Redis: it saves a round trip on
every search and keeps regions available while Redis is down (RF-15, plan-D10).
Region and destination ids are ephemeral Alcampo identifiers, not secrets.
"""

import logging
import time
from collections.abc import Callable
from typing import Final

from pydantic import BaseModel, ConfigDict, ValidationError
from redis.asyncio import Redis
from redis.exceptions import RedisError

NOT_SERVED: Final = "NOT_SERVED"

logger = logging.getLogger(__name__)


class Region(BaseModel):
    """A region Alcampo serves, as used to search and to renew its session."""

    model_config = ConfigDict(frozen=True)

    region_id: str
    retailer_region_id: str
    delivery_destination_id: str


def _postal_code_key(postal_code: str) -> str:
    return f"postal-code-region:{postal_code}"


def _region_key(region_id: str) -> str:
    return f"region:{region_id}"


class RegionMemory:
    """Per-process L1 with its own expiry; one instance for the app (`lifespan`)."""

    def __init__(self, *, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._entries: dict[str, tuple[str, float]] = {}

    def get(self, key: str) -> str | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if self._now() >= expires_at:
            del self._entries[key]
            return None
        return value

    def set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        self._entries[key] = (value, self._now() + ttl_seconds)

    def delete(self, key: str) -> None:
        self._entries.pop(key, None)


class RegionRepository:
    """Read and write region records: memory first, then Redis (spec 007 RF-3, RF-4, RF-15)."""

    def __init__(
        self,
        redis: Redis,
        *,
        memory: RegionMemory,
        ttl_seconds: int,
        negative_ttl_seconds: int,
    ) -> None:
        self._redis = redis
        self._memory = memory
        self._ttl = ttl_seconds
        self._negative_ttl = negative_ttl_seconds

    async def region_id_for(self, postal_code: str) -> str | None:
        """The region id of `postal_code`, `NOT_SERVED`, or `None` if unknown."""
        return await self._get(_postal_code_key(postal_code))

    async def region(self, region_id: str) -> Region | None:
        raw = await self._get(_region_key(region_id))
        if raw is None:
            return None
        try:
            return Region.model_validate_json(raw)
        except ValidationError:
            logger.warning("corrupted region record treated as missing region=%s", region_id)
            return None

    async def save_postal_code(self, postal_code: str, region_id: str) -> None:
        await self._set(_postal_code_key(postal_code), region_id, ttl_seconds=self._ttl)

    async def save_not_served(self, postal_code: str) -> None:
        await self._set(_postal_code_key(postal_code), NOT_SERVED, ttl_seconds=self._negative_ttl)

    async def save_region(self, region: Region) -> None:
        await self._set(
            _region_key(region.region_id), region.model_dump_json(), ttl_seconds=self._ttl
        )

    async def forget_region(self, region_id: str) -> None:
        """Drop a region whose destination no longer works (plan-D6)."""
        key = _region_key(region_id)
        self._memory.delete(key)
        try:
            await self._redis.delete(key)
        except RedisError:
            logger.warning("redis unavailable op=region.forget, degraded")

    async def _get(self, key: str) -> str | None:
        value = self._memory.get(key)
        if value is not None:
            return value
        try:
            raw = await self._redis.get(key)
            ttl = await self._redis.ttl(key) if raw is not None else 0
        except RedisError:
            logger.warning("redis unavailable op=region.get, degraded")
            return None
        if raw is None:
            return None
        value = raw.decode() if isinstance(raw, bytes) else str(raw)
        if ttl > 0:
            # Only as long as Redis keeps it, so memory never outlives the record.
            self._memory.set(key, value, ttl_seconds=ttl)
        return value

    async def _set(self, key: str, value: str, *, ttl_seconds: int) -> None:
        self._memory.set(key, value, ttl_seconds=ttl_seconds)
        try:
            await self._redis.set(key, value, ex=ttl_seconds)
        except RedisError:
            logger.warning("redis unavailable op=region.set, degraded")
