"""Retry helper for outbound calls to Alcampo.

`send_with_retry` is a pure function: it takes a `send` callable and an
injectable `sleep`, so tests never wait for real time (plan-D3).
"""

import asyncio
import logging
import math
import random
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.exceptions import UpstreamBlockedError, UpstreamUnavailableError

Sleep = Callable[[float], Awaitable[None]]
Uniform = Callable[[float, float], float]

WAF_CHALLENGE_HEADER = "x-amzn-waf-action"

logger = logging.getLogger(__name__)

MAX_RETRY_AFTER_WAIT_SECONDS = 60.0

_RETRY_AFTER_SECONDS = re.compile(r"^[0-9]+$")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def parse_retry_after(value: str | None, *, now: datetime) -> float | None:
    """Parse a `Retry-After` header into seconds to wait (spec 002 RF-5..RF-7, plan-D7).

    Accepts the two HTTP formats: non-negative integer seconds, or an HTTP date.
    A date in the past means "retry now" (0.0). Anything else returns `None`, so
    the caller falls back to exponential backoff; so does a value that is no
    finite wait, such as a date whose year overflows or hundreds of digits
    (spec 015 RF-2).
    """
    if value is None:
        return None
    value = value.strip()
    if _RETRY_AFTER_SECONDS.match(value):
        seconds = float(value)
        return seconds if math.isfinite(seconds) else None
    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):  # a year too big overflows
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
    now: Callable[[], datetime] = _utc_now,
    url: str = "-",
) -> httpx.Response:
    """Call `send`, retrying transient failures with exponential backoff.

    - 2xx (without a WAF challenge header): returned immediately (RF-3).
    - 5xx, 429, request errors: retried up to `max_attempts` (RF-15). Any
      `httpx.RequestError`, not only transport ones: a badly compressed body
      (`DecodingError`) or a redirect loop must not become a 500 (spec 015 RF-1).
      Exhausting retries raises `UpstreamUnavailableError` (RF-17).
    - Any other 4xx: raises immediately, no retry (RF-16).
    - A WAF challenge (`x-amzn-waf-action` header, arrives as an empty 202)
      raises immediately, no retry, regardless of status code (RF-18, spec-D5).
      Checked before status classification: the challenge can arrive as a 2xx.

    Every wait adds a random jitter in `[0, jitter_max]` (spec 002 RF-9,
    plan-D6). `jitter_max=0.0` keeps the exact backoff of spec 001 (plan-D9).

    On a 429, `Retry-After` (seconds or HTTP date) replaces the backoff, and the
    final wait, jitter included, is capped at 60 s (spec 002 RF-5..RF-8,
    plan-D8). `Retry-After` is ignored on any other status (spec-D4).

    Logging (spec 003 RF-8..RF-10): a WARNING per retry, an ERROR when retries
    are exhausted or a 4xx is not retryable. The WAF challenge is logged by the
    service, which knows the cooldown (plan-D8). `url` is passed explicitly
    rather than read from httpx objects (plan-D7) and logged with %r (RF-18).
    """

    async def wait_before_retry(attempt: int, reason: str, wait: float) -> None:
        logger.warning(
            "retrying attempt=%d reason=%s url=%r wait_s=%.2f", attempt, reason, url, wait
        )
        await sleep(wait)

    def exhausted(attempt: int, reason: str) -> None:
        logger.error("retries exhausted attempts=%d reason=%s url=%r", attempt, reason, url)

    def backoff(attempt: int) -> float:
        return base_delay * 2.0 ** (attempt - 1) + uniform(0, jitter_max)

    def rate_limited_wait(response: httpx.Response, attempt: int) -> float:
        retry_after = parse_retry_after(response.headers.get("Retry-After"), now=now())
        if retry_after is None:
            wait = backoff(attempt)
        else:
            wait = retry_after + uniform(0, jitter_max)
        return min(wait, MAX_RETRY_AFTER_WAIT_SECONDS)

    last_transport_error: httpx.RequestError | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = await send()
        except httpx.RequestError as exc:
            last_transport_error = exc
            reason = type(exc).__name__
            if attempt == max_attempts:
                exhausted(attempt, reason)
                raise UpstreamUnavailableError("transport error, retries exhausted") from exc
            await wait_before_retry(attempt, reason, backoff(attempt))
            continue

        if _is_waf_challenge(response):
            raise UpstreamBlockedError("WAF challenge")

        if response.is_success:
            return response

        if not _is_retryable(response):
            logger.error("non-retryable upstream status=%d url=%r", response.status_code, url)
            raise UpstreamUnavailableError(
                f"non-retryable upstream status {response.status_code}",
                status_code=response.status_code,
            )

        reason = f"status {response.status_code}"
        if attempt == max_attempts:
            exhausted(attempt, reason)
            raise UpstreamUnavailableError(f"upstream returned {response.status_code}")

        if response.status_code == 429:
            await wait_before_retry(attempt, reason, rate_limited_wait(response, attempt))
        else:
            await wait_before_retry(attempt, reason, backoff(attempt))

    # Unreachable: the loop above always returns or raises before completing.
    raise UpstreamUnavailableError("retries exhausted") from last_transport_error
