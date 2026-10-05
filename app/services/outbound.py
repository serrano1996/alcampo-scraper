"""What every request to Alcampo goes through before leaving (spec 010).

Built piece by piece: `OutboundPacer` spaces the requests of this process.
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable

Sleep = Callable[[float], Awaitable[None]]
Uniform = Callable[[float, float], float]


class OutboundPacer:
    """Leave at least `min_interval_ms` (+ jitter) between two requests of this process.

    A region resolution used to send ~10 requests in 1-3 s (spec 007, T13); spaced,
    it takes ~7 s and looks less like a bot (spec 010 RF-3, plan-D3). Concurrent
    callers queue behind a lock and leave one after another. Waiting takes no
    quota and runs inside the caller's own time budget (spec 008 RF-7), so it can
    never hang a request. `min_interval_ms == 0` disables it.
    """

    def __init__(
        self,
        *,
        min_interval_ms: int,
        jitter_ms: int,
        now: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
        uniform: Uniform = random.uniform,
    ) -> None:
        self._min = min_interval_ms / 1000
        self._jitter = jitter_ms / 1000
        self._now = now
        self._sleep = sleep
        self._uniform = uniform
        self._next_at = 0.0
        self._lock = asyncio.Lock()

    async def wait_turn(self) -> float:
        """Wait until this process may send its next request; return the seconds waited."""
        if self._min == 0:
            return 0.0
        async with self._lock:
            wait = max(0.0, self._next_at - self._now())
            if wait > 0:
                await self._sleep(wait)
            self._next_at = self._now() + self._min + self._uniform(0, self._jitter)
            return wait
