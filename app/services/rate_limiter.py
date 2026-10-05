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
from app.services.redis_circuit import RedisCircuitBreaker, redis_unavailable

RATE_LIMIT_KEY = "ratelimit:alcampo"
# The long window of spec 010 (RF-1): same rules, its own key.
RATE_LIMIT_LONG_KEY = "ratelimit:alcampo:long"

Clock = Callable[[], float]

logger = logging.getLogger(__name__)


class LocalRateLimiter:
    """In-process sliding window used while Redis is unavailable.

    One instance per limited resource, created in the `lifespan`: limiters are
    built per request, so a per-limiter window would limit nothing.
    """

    def __init__(self) -> None:
        self._sent: deque[tuple[float, str]] = deque()

    def acquire(self, *, now: float, limit: int, window_seconds: int, slot: str) -> None:
        # Same boundary as ZREMRANGEBYSCORE -inf now-window: an entry exactly
        # `window_seconds` old has left the window.
        while self._sent and self._sent[0][0] <= now - window_seconds:
            self._sent.popleft()
        if len(self._sent) >= limit:
            raise OutboundRateLimitedError("outbound rate limit reached")
        self._sent.append((now, slot))

    def release(self, slot: str) -> bool:
        """Give `slot` back; `False` if this window does not hold it."""
        for entry in self._sent:
            if entry[1] == slot:
                self._sent.remove(entry)
                return True
        return False


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
        circuit: RedisCircuitBreaker | None = None,
    ) -> None:
        self._redis = redis
        self._limit = limit
        self._window = window_seconds
        self._now = now
        self._key = key
        # Production passes the shared one from app.state; a private window only
        # ever limits this instance.
        self._fallback = fallback if fallback is not None else LocalRateLimiter()
        self._circuit = circuit if circuit is not None else RedisCircuitBreaker(open_seconds=0)

    async def acquire(self) -> str:
        """Take one slot for a request about to be sent, or raise if none is left.

        Returns the slot's id, to give it back with `release` (spec 010 plan-D2:
        a request rejected by another window must not consume this one).
        Called before every real request, retries included. `limit == 0`
        disables the limit, never touches Redis and returns `""`.
        """
        if self._limit == 0:
            return ""
        now = self._now()
        slot = uuid.uuid4().hex
        try:
            await self._circuit.call(lambda: self._acquire_in_redis(now, slot))
        except RedisError as exc:
            redis_unavailable(logger, f"rate_limit key={self._key}", exc)
            self._fallback.acquire(
                now=now, limit=self._limit, window_seconds=self._window, slot=slot
            )
        return slot

    async def release(self, slot: str) -> None:
        """Give back a slot taken by `acquire` (no-op for `""`)."""
        if not slot or self._fallback.release(slot):
            return
        try:
            await self._circuit.call(lambda: self._redis.zrem(self._key, slot))
        except RedisError as exc:
            redis_unavailable(logger, f"rate_limit.release key={self._key}", exc)

    async def _acquire_in_redis(self, now: float, member: str) -> None:
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
