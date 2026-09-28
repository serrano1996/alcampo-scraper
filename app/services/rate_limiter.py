"""Global limit on outbound requests to Alcampo, shared through Redis (spec 008 RF-3, RF-6).

The WAF cooldown (spec 002) reacts once the egress IP is already blocked; this
limit acts before, so many distinct uncached searches cannot burst.

Sliding window over a sorted set, not a fixed window (plan-D3): a fixed window
lets through twice the limit around its boundary, which is the very burst to
avoid. One member per request, scored with its timestamp.

Without Redis (down or past its timeout) each process falls back to a local
window with the same rules (spec 007 RF-15, plan-D10): coordination between
instances is lost, but no process can burst.
"""

import logging
import time
import uuid
from collections import deque
from collections.abc import Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.exceptions import OutboundRateLimitedError

RATE_LIMIT_KEY = "ratelimit:alcampo"

Clock = Callable[[], float]

logger = logging.getLogger(__name__)


class LocalRateLimiter:
    """In-process sliding window used while Redis is unavailable.

    One instance per limited resource, created in the `lifespan`: limiters are
    built per request, so a per-limiter window would limit nothing.
    """

    def __init__(self) -> None:
        self._sent: deque[float] = deque()

    def acquire(self, *, now: float, limit: int, window_seconds: int) -> None:
        # Same boundary as ZREMRANGEBYSCORE -inf now-window: an entry exactly
        # `window_seconds` old has left the window.
        while self._sent and self._sent[0] <= now - window_seconds:
            self._sent.popleft()
        if len(self._sent) >= limit:
            raise OutboundRateLimitedError("outbound rate limit reached")
        self._sent.append(now)


class OutboundRateLimiter:
    """Admit at most `limit` requests per `window_seconds` across every instance."""

    def __init__(
        self,
        redis: Redis,
        *,
        limit: int,
        window_seconds: int,
        now: Clock = time.time,
        key: str = RATE_LIMIT_KEY,
        fallback: LocalRateLimiter | None = None,
    ) -> None:
        self._redis = redis
        self._limit = limit
        self._window = window_seconds
        self._now = now
        self._key = key
        # Production passes the shared one from app.state; a private window only
        # ever limits this instance.
        self._fallback = fallback if fallback is not None else LocalRateLimiter()

    async def acquire(self) -> None:
        """Take one slot for a request about to be sent, or raise if none is left.

        Called before every real request, retries included. `limit == 0`
        disables the limit and never touches Redis.
        """
        if self._limit == 0:
            return
        now = self._now()
        try:
            await self._acquire_in_redis(now)
        except RedisError:
            logger.warning("redis unavailable op=rate_limit key=%s, degraded", self._key)
            self._fallback.acquire(now=now, limit=self._limit, window_seconds=self._window)

    async def _acquire_in_redis(self, now: float) -> None:
        member = uuid.uuid4().hex
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(self._key, "-inf", now - self._window)
            pipe.zadd(self._key, {member: now})
            pipe.zcard(self._key)
            pipe.expire(self._key, self._window)
            _, _, count, _ = await pipe.execute()
        if count > self._limit:
            # A rejected attempt must not consume quota, or a burst of rejected
            # searches would keep the limit exhausted forever.
            await self._redis.zrem(self._key, member)
            raise OutboundRateLimitedError("outbound rate limit reached")
