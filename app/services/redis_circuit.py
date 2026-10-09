"""Circuit breaker in front of Redis (spec 007 RF-18, plan-D13).

Without it, a search with Redis down or hung waited each Redis operation's
timeout (2 s) about four times: ~9 s per search (T13 manual check). With the
container stopped, each attempt also left an unretrieved DNS error that asyncio
logs as an ERROR. After one failure the circuit opens and, for
`open_seconds`, every operation fails at once with `RedisCircuitOpenError`,
so repositories go straight to their local fallbacks (spec 007 RF-15).

`RedisCircuitOpenError` is a `RedisError`: repositories already fall back on
that, so their error handling does not change.

After the period the next operation probes Redis: success closes the circuit,
failure opens it again. Only one probe runs at a time; meanwhile the others still
see the circuit open, so against a hung Redis one request pays the timeout, not
every request in flight (spec 015 RF-7). A probe that ends without an answer
from Redis (a bug, a cancellation) lets the next call probe.
"""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from redis.exceptions import RedisError

T = TypeVar("T")

logger = logging.getLogger(__name__)


class RedisCircuitOpenError(RedisError):
    """Redis was skipped because the circuit is open (no request was made)."""


class RedisCircuitBreaker:
    """One per process (`lifespan`), shared by every Redis-backed repository."""

    def __init__(self, *, open_seconds: int, now: Callable[[], float] = time.monotonic) -> None:
        self._open_seconds = open_seconds
        self._now = now
        self._open_until: float | None = None
        self._probing = False

    async def call(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Run `operation` unless the circuit is open. `open_seconds == 0` never opens."""
        if self._open_seconds == 0:
            return await operation()
        open_until = self._open_until
        probing = open_until is not None
        if open_until is not None:
            if self._probing or self._now() < open_until:
                raise RedisCircuitOpenError("redis circuit open")
            self._probing = True
        try:
            result = await operation()
        except RedisError as exc:
            self._open_until = self._now() + self._open_seconds
            logger.warning(
                "redis circuit open for %ds after %s", self._open_seconds, type(exc).__name__
            )
            raise
        finally:
            if probing:
                self._probing = False
        if probing:
            self._open_until = None
            logger.info("redis circuit closed: redis answered again")
        return result


def redis_unavailable(logger_: logging.Logger, op: str, exc: RedisError) -> None:
    """The repositories' "degraded" line: WARNING on a real failure, DEBUG while open.

    The circuit already logged its WARNING when it opened; repeating one per
    skipped operation would bring back the noise it removes (spec-D12).
    """
    level = logging.DEBUG if isinstance(exc, RedisCircuitOpenError) else logging.WARNING
    logger_.log(level, "redis unavailable op=%s, degraded", op)
