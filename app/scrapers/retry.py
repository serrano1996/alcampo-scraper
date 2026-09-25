"""Retry helper for outbound calls to Alcampo.

`send_with_retry` is a pure function: it takes a `send` callable and an
injectable `sleep`, so tests never wait for real time (plan-D3).
"""

import asyncio
import random
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.exceptions import UpstreamUnavailableError

Sleep = Callable[[float], Awaitable[None]]
Uniform = Callable[[float, float], float]

WAF_CHALLENGE_HEADER = "x-amzn-waf-action"

_RETRY_AFTER_SECONDS = re.compile(r"^[0-9]+$")


def parse_retry_after(value: str | None, *, now: datetime) -> float | None:
    """Parse a `Retry-After` header into seconds to wait (spec 002 RF-5..RF-7, plan-D7).

    Accepts the two HTTP formats: non-negative integer seconds, or an HTTP date.
    A date in the past means "retry now" (0.0). Anything else returns `None`, so
    the caller falls back to exponential backoff.
    """
    if value is None:
        return None
    value = value.strip()
    if _RETRY_AFTER_SECONDS.match(value):
        return float(value)
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=UTC)
    return max((retry_at - now).total_seconds(), 0.0)


def _is_waf_challenge(response: httpx.Response) -> bool:
    return WAF_CHALLENGE_HEADER in response.headers


def _is_retryable(response: httpx.Response) -> bool:
    return response.status_code >= 500 or response.status_code == 429


async def send_with_retry(
    send: Callable[[], Awaitable[httpx.Response]],
    *,
    max_attempts: int,
    base_delay: float,
    jitter_max: float = 0.0,
    sleep: Sleep = asyncio.sleep,
    uniform: Uniform = random.uniform,
) -> httpx.Response:
    """Call `send`, retrying transient failures with exponential backoff.

    - 2xx (without a WAF challenge header): returned immediately (RF-3).
    - 5xx, 429, transport errors: retried up to `max_attempts` (RF-15).
      Exhausting retries raises `UpstreamUnavailableError` (RF-17).
    - Any other 4xx: raises immediately, no retry (RF-16).
    - A WAF challenge (`x-amzn-waf-action` header, arrives as an empty 202)
      raises immediately, no retry, regardless of status code (RF-18, spec-D5).
      Checked before status classification: the challenge can arrive as a 2xx.

    Every wait adds a random jitter in `[0, jitter_max]` (spec 002 RF-9,
    plan-D6). `jitter_max=0.0` keeps the exact backoff of spec 001 (plan-D9).
    """

    def backoff(attempt: int) -> float:
        return base_delay * 2 ** (attempt - 1) + uniform(0, jitter_max)

    last_transport_error: httpx.TransportError | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = await send()
        except httpx.TransportError as exc:
            last_transport_error = exc
            if attempt == max_attempts:
                raise UpstreamUnavailableError("transport error, retries exhausted") from exc
            await sleep(backoff(attempt))
            continue

        if _is_waf_challenge(response):
            raise UpstreamUnavailableError("WAF challenge")

        if response.is_success:
            return response

        if not _is_retryable(response):
            raise UpstreamUnavailableError(f"non-retryable upstream status {response.status_code}")

        if attempt == max_attempts:
            raise UpstreamUnavailableError(f"upstream returned {response.status_code}")

        await sleep(backoff(attempt))

    # Unreachable: the loop above always returns or raises before completing.
    raise UpstreamUnavailableError("retries exhausted") from last_transport_error
