"""Retry helper for outbound calls to Alcampo.

`send_with_retry` is a pure function: it takes a `send` callable and an
injectable `sleep`, so tests never wait for real time (plan-D3).
"""

import asyncio
from collections.abc import Awaitable, Callable

import httpx

from app.exceptions import UpstreamUnavailableError

Sleep = Callable[[float], Awaitable[None]]

WAF_CHALLENGE_HEADER = "x-amzn-waf-action"


def _is_waf_challenge(response: httpx.Response) -> bool:
    return WAF_CHALLENGE_HEADER in response.headers


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

    - 2xx (without a WAF challenge header): returned immediately (RF-3).
    - 5xx, 429, transport errors: retried up to `max_attempts` (RF-15).
      Exhausting retries raises `UpstreamUnavailableError` (RF-17).
    - Any other 4xx: raises immediately, no retry (RF-16).
    - A WAF challenge (`x-amzn-waf-action` header, arrives as an empty 202)
      raises immediately, no retry, regardless of status code (RF-18, spec-D5).
      Checked before status classification: the challenge can arrive as a 2xx.
    """
    last_transport_error: httpx.TransportError | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = await send()
        except httpx.TransportError as exc:
            last_transport_error = exc
            if attempt == max_attempts:
                raise UpstreamUnavailableError("transport error, retries exhausted") from exc
            await sleep(base_delay * 2 ** (attempt - 1))
            continue

        if _is_waf_challenge(response):
            raise UpstreamUnavailableError("WAF challenge")

        if response.is_success:
            return response

        if not _is_retryable(response):
            raise UpstreamUnavailableError(f"non-retryable upstream status {response.status_code}")

        if attempt == max_attempts:
            raise UpstreamUnavailableError(f"upstream returned {response.status_code}")

        await sleep(base_delay * 2 ** (attempt - 1))

    # Unreachable: the loop above always returns or raises before completing.
    raise UpstreamUnavailableError("retries exhausted") from last_transport_error
