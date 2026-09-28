"""Groups simultaneous identical searches into one upstream request (spec 008 RF-1).

The cache only protects after the first response is stored: ten clients
searching the same uncached term at once used to send ten requests to Alcampo,
the kind of burst its WAF punishes. The first caller for a key starts the fetch;
later callers wait for the same task.

One instance per process, created in the `lifespan` (plan-D2): services are
built per request, so a per-service registry would group nothing. Grouping
across instances is left to the global rate limit (spec-D1).
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Generic, TypeVar

T = TypeVar("T")


class InFlightSearches(Generic[T]):
    """Registry of the fetches currently running, keyed by normalized search."""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[T]] = {}

    async def run(self, key: str, fetch: Callable[[], Awaitable[T]]) -> tuple[T, bool]:
        """Return `(result, shared)`: `shared` is `False` only for the caller that fetched.

        The result, or the exception, reaches every caller. Each one waits
        through `asyncio.shield`, so a client that disconnects cancels its own
        wait, never the fetch the others depend on.
        """
        task = self._tasks.get(key)
        shared = task is not None
        if task is None:
            task = asyncio.create_task(self._call(fetch))
            self._tasks[key] = task
            task.add_done_callback(lambda done: self._forget(key, done))
        return await asyncio.shield(task), shared

    @staticmethod
    async def _call(fetch: Callable[[], Awaitable[T]]) -> T:
        return await fetch()

    def _forget(self, key: str, done: asyncio.Task[T]) -> None:
        if self._tasks.get(key) is done:
            del self._tasks[key]
        if not done.cancelled():
            # Marks the exception as retrieved: if every waiter was cancelled,
            # asyncio would otherwise log "Task exception was never retrieved".
            done.exception()
