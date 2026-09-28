"""Redis marker that pauses outbound searches after an AWS WAF challenge.

Alcampo's WAF blocks the whole egress IP for 2-4 minutes (Fase 0 §5), so the
marker is a single global key, not one per term or region (spec 002 RNF-4).
Every instance behind the same IP shares it through Redis.

The cooldown grows when challenges repeat (spec 008 RF-8, plan-D7): a fixed
180 s can fall short of a 4-minute block, and hitting Alcampo right after it
renews the block. A second key remembers the last duration for
`max_seconds`, which is what "recent" means.

Without Redis each process falls back to a local marker with the same rules
(spec 007 RF-15, plan-D10).
"""

import logging
import time
from collections.abc import Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

WAF_COOLDOWN_KEY = "waf:cooldown"
WAF_COOLDOWN_LAST_KEY = "waf:cooldown:last"

logger = logging.getLogger(__name__)


def _grow(last: int | None, *, base_seconds: int, max_seconds: int) -> int:
    """180 -> 360 -> 720 -> 900: double the previous duration, capped (spec 008 RF-8)."""
    return base_seconds if last is None else min(last * 2, max_seconds)


class LocalCooldown:
    """In-process cooldown used while Redis is unavailable; one per process (`lifespan`)."""

    def __init__(self, *, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._until = 0.0
        self._last: int | None = None
        self._last_at = 0.0

    def is_active(self) -> bool:
        return self._now() < self._until

    def activate(self, *, base_seconds: int, max_seconds: int) -> int:
        if base_seconds <= 0:
            return 0
        now = self._now()
        recent = self._last if now - self._last_at < max_seconds else None
        duration = _grow(recent, base_seconds=base_seconds, max_seconds=max_seconds)
        self._until = now + duration
        self._last, self._last_at = duration, now
        return duration


class WafCooldownRepository:
    """Read and set the WAF cooldown marker (spec 002 RF-15, RF-18, RF-19; spec 008 RF-8)."""

    def __init__(self, redis: Redis, *, fallback: LocalCooldown | None = None) -> None:
        self._redis = redis
        # Production passes the shared one from app.state (see LocalCooldown).
        self._fallback = fallback if fallback is not None else LocalCooldown()

    async def is_active(self) -> bool:
        """Return `True` while the marker exists; Redis expiry ends the cooldown."""
        try:
            return bool(await self._redis.exists(WAF_COOLDOWN_KEY))
        except RedisError:
            logger.warning("redis unavailable op=cooldown.is_active, degraded")
            return self._fallback.is_active()

    async def activate(self, *, base_seconds: int, max_seconds: int) -> int:
        """Start a cooldown and return its duration in seconds (0 = disabled).

        The first challenge gets `base_seconds`; one within `max_seconds` of the
        previous gets double the previous duration, capped at `max_seconds`
        (180 -> 360 -> 720 -> 900 with the defaults). Always overwritten, never
        `NX`: a new challenge re-confirms the block (spec 002 plan-D5).
        """
        if base_seconds <= 0:
            return 0
        try:
            last = await self._redis.get(WAF_COOLDOWN_LAST_KEY)
            duration = _grow(
                None if last is None else int(last),
                base_seconds=base_seconds,
                max_seconds=max_seconds,
            )
            await self._redis.set(WAF_COOLDOWN_KEY, "1", ex=duration)
            await self._redis.set(WAF_COOLDOWN_LAST_KEY, str(duration), ex=max_seconds)
        except RedisError:
            logger.warning("redis unavailable op=cooldown.activate, degraded")
            return self._fallback.activate(base_seconds=base_seconds, max_seconds=max_seconds)
        return duration
