"""What every request to Alcampo goes through before leaving (spec 010).

`OutboundPacer` spaces the requests of this process; `TrafficLog` remembers
what was sent, for the breakdown logged with every WAF challenge.
"""

import asyncio
import random
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

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


KINDS = ("search", "resolution", "session")
WINDOWS = (("1m", 60), ("5m", 300), ("15m", 900))
HORIZON_SECONDS = 900


@dataclass(frozen=True)
class TrafficSummary:
    """Requests per kind in the last 1/5/15 minutes, and 4xx answers in the last 15."""

    counts: dict[str, dict[str, int]]
    client_errors_15m: int

    def __str__(self) -> str:
        windows = " ".join(
            f"{name}[" + " ".join(f"{kind}={n}" for kind, n in by_kind.items()) + "]"
            for name, by_kind in self.counts.items()
        )
        return f"{windows} 4xx_15m={self.client_errors_15m}"


class TrafficLog:
    """What this instance sent to Alcampo in the last 15 minutes (spec 010 RF-6, plan-D4).

    Logged with every WAF challenge, so the outbound limits can be tuned with
    production data instead of provoking more blocks. Per process on purpose:
    it describes what this instance sent, which is what can be corrected.
    """

    def __init__(self, *, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self._requests: deque[tuple[float, str]] = deque()
        self._client_errors: deque[float] = deque()

    def record(self, kind: str) -> None:
        """A request of `kind` (`search`, `resolution` or `session`) is about to leave."""
        self._requests.append((self._now(), kind))
        self._prune()

    def record_status(self, status: int) -> None:
        """An answer arrived; only 4xx are kept (spec 010 RF-5, RF-6)."""
        if 400 <= status < 500:
            self._client_errors.append(self._now())
        self._prune()

    def summary(self) -> TrafficSummary:
        self._prune()
        now = self._now()
        counts = {
            name: {
                kind: sum(1 for at, k in self._requests if k == kind and now - at <= seconds)
                for kind in KINDS
            }
            for name, seconds in WINDOWS
        }
        return TrafficSummary(counts=counts, client_errors_15m=len(self._client_errors))

    def __len__(self) -> int:
        return len(self._requests) + len(self._client_errors)

    def _prune(self) -> None:
        oldest = self._now() - HORIZON_SECONDS
        while self._requests and self._requests[0][0] < oldest:
            self._requests.popleft()
        while self._client_errors and self._client_errors[0] < oldest:
            self._client_errors.popleft()
