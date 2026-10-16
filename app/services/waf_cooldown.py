"""Redis marker that pauses outbound searches after an AWS WAF challenge.

Alcampo's WAF blocks the whole egress IP for 2-4 minutes (Fase 0 §5), so the
marker is a single global key, not one per term or region (spec 002 RNF-4).
Every instance behind the same IP shares it through Redis.

The cooldown grows when challenges repeat (spec 008 RF-8, plan-D7): a fixed
180 s can fall short of a 4-minute block, and hitting Alcampo right after it
renews the block. A second key remembers the last duration for
`max_seconds`, which is what "recent" means.

Without Redis each process falls back to a local marker with the same rules
(spec 007 RF-15, plan-D10). Every challenge also holds Redis' cooldown locally,
even one another instance started, so it still holds if Redis dies during it, and the
growth goes on from Redis' last duration (spec 015 RF-9, plan-D8). While Redis
answers, Redis decides: clearing the key ends the cooldown everywhere.
"""

import logging
import math
import time
from collections.abc import Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.services.redis_circuit import RedisCircuitBreaker, redis_unavailable

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
        if now < self._until:
            # Same block (spec 016 RF-1): neither grown nor extended.
            return math.ceil(self._until - now)
        recent = self._last if now - self._last_at < max_seconds else None
        duration = _grow(recent, base_seconds=base_seconds, max_seconds=max_seconds)
        self._until = now + duration
        self._last, self._last_at = duration, now
        return duration

    def hold(self, remaining: int, *, last: int) -> None:
        """Mirror the cooldown in Redis: same end and same last duration."""
        now = self._now()
        self._until = max(self._until, now + remaining)
        self._last, self._last_at = last, now


class WafCooldownRepository:
    """Read and set the WAF cooldown marker (spec 002 RF-15, RF-18, RF-19; spec 008 RF-8)."""

    def __init__(
        self,
        redis: Redis,
        *,
        fallback: LocalCooldown | None = None,
        circuit: RedisCircuitBreaker | None = None,
    ) -> None:
        self._redis = redis
        # Production passes the shared one from app.state (see LocalCooldown).
        self._fallback = fallback if fallback is not None else LocalCooldown()
        self._circuit = circuit if circuit is not None else RedisCircuitBreaker(open_seconds=0)

    async def is_active(self) -> bool:
        """Return `True` while the marker exists; Redis expiry ends the cooldown."""
        try:
            return bool(await self._circuit.call(lambda: self._redis.exists(WAF_COOLDOWN_KEY)))
        except RedisError as exc:
            redis_unavailable(logger, "cooldown.is_active", exc)
            return self._fallback.is_active()

    async def activate(self, *, base_seconds: int, max_seconds: int) -> int:
        """Start a cooldown and return its duration in seconds (0 = disabled).

        The first challenge gets `base_seconds`; one within `max_seconds` of the
        previous gets double the previous duration, capped at `max_seconds`
        (180 -> 360 -> 720 -> 900 with the defaults). A challenge while one is
        active returns what is left and changes nothing: no request leaves during
        a cooldown, so it comes from one sent before, and confirms the same block
        (spec 016 RF-1; it replaces spec 002 plan-D5's "always overwritten").
        """
        if base_seconds <= 0:
            return 0

        async def in_force() -> tuple[int, int] | None:
            """`(seconds left, full duration)` of the cooldown in force, if any.

            Never rewritten, even without `last` (spec 017 RF-2). PTTL, not TTL:
            a key in its last half second is still a cooldown (RF-3).
            """
            pttl = await self._redis.pttl(WAF_COOLDOWN_KEY)
            if pttl == -2:
                return None
            # -1: set by hand without expiry, respected (spec 017 spec-D3).
            left = base_seconds if pttl < 0 else max(1, math.ceil(pttl / 1000))
            last = await self._redis.get(WAF_COOLDOWN_LAST_KEY)
            return left, max(left, 0 if last is None else int(last))

        async def activate_in_redis() -> tuple[int, int]:
            current = await in_force()
            if current is not None:
                return current
            last = await self._redis.get(WAF_COOLDOWN_LAST_KEY)
            duration = _grow(
                None if last is None else int(last),
                base_seconds=base_seconds,
                max_seconds=max_seconds,
            )
            # NX: one start per block; whoever loses joins the winner's (spec 017 RF-1).
            if not await self._redis.set(WAF_COOLDOWN_KEY, "1", ex=duration, nx=True):
                return await in_force() or (duration, duration)
            await self._redis.set(WAF_COOLDOWN_LAST_KEY, str(duration), ex=max_seconds)
            return duration, duration

        try:
            left, duration = await self._circuit.call(activate_in_redis)
        except RedisError as exc:
            redis_unavailable(logger, "cooldown.activate", exc)
            return self._fallback.activate(base_seconds=base_seconds, max_seconds=max_seconds)
        # Every challenge arms the local one too, also when it joins a cooldown
        # another instance started (spec 015 RF-9); its growth goes on from the
        # full duration, never from what was left.
        self._fallback.hold(left, last=duration)
        return left
