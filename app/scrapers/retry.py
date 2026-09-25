"""Retry helper for outbound calls to Alcampo.

`send_with_retry` is a pure function: it takes a `send` callable and an
injectable `sleep`, so tests never wait for real time (plan-D3).
"""

import asyncio
from collections.abc import Awaitable, Callable

import httpx

Sleep = Callable[[float], Awaitable[None]]


def _is_retryable(response: httpx.Response) -> bool:
    return response.status_code >= 500 or response.status_code == 429


async def send_with_retry(
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    max_attempts: int,
    base_delay: float,
    sleep: Sleep = asyncio.sleep,
) -> httpx.Response:
    """Call `send`, retrying transient failures with exponential backoff.

    Only classifies retryable outcomes (5xx, 429, transport errors) for now.
    Non-retryable responses and exhausted retries are returned/raised as-is;
    translating them into `UpstreamUnavailableError` is completed in T11.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            response = await send()
        except httpx.TransportError:
            if attempt == max_attempts:
                raise
            await sleep(base_delay * 2 ** (attempt - 1))
            continue

        if attempt == max_attempts or not _is_retryable(response):
            return response

        await sleep(base_delay * 2 ** (attempt - 1))

    raise AssertionError("unreachable: loop always returns or raises")
