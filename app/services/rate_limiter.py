"""Global limit on outbound requests to Alcampo, shared through Redis (spec 008 RF-3, RF-6).

The WAF cooldown (spec 002) reacts once the egress IP is already blocked; this
limit acts before, so many distinct uncached searches cannot burst.

Sliding window over a sorted set, not a fixed window (plan-D3): a fixed window
lets through twice the limit around its boundary, which is the very burst to
avoid. One member per request, scored with its timestamp.
"""

import time
import uuid
from collections.abc import Callable

from redis.asyncio import Redis

from app.exceptions import OutboundRateLimitedError

RATE_LIMIT_KEY = "ratelimit:alcampo"

Clock = Callable[[], float]


class OutboundRateLimiter:
    """Admit at most `limit` requests per `window_seconds` across every instance."""

    def __init__(
        self,
        redis: Redis,
        *,
        limit: int,
        window_seconds: int,
        now: Clock = time.time,
    ) -> None:
        self._redis = redis
        self._limit = limit
        self._window = window_seconds
        self._now = now

    async def acquire(self) -> None:
        """Take one slot for a request about to be sent, or raise if none is left.

        Called before every real request, retries included. `limit == 0`
        disables the limit and never touches Redis.
        """
        if self._limit == 0:
            return
        now = self._now()
        member = uuid.uuid4().hex
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(RATE_LIMIT_KEY, "-inf", now - self._window)
            pipe.zadd(RATE_LIMIT_KEY, {member: now})
            pipe.zcard(RATE_LIMIT_KEY)
            pipe.expire(RATE_LIMIT_KEY, self._window)
            _, _, count, _ = await pipe.execute()
        if count > self._limit:
            # A rejected attempt must not consume quota, or a burst of rejected
            # searches would keep the limit exhausted forever.
            await self._redis.zrem(RATE_LIMIT_KEY, member)
            raise OutboundRateLimitedError("outbound rate limit reached")
