"""Client for Alcampo's product search endpoint (webproductpagews v6)."""

import json
import logging

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.exceptions import UpstreamUnavailableError
from app.models.alcampo import AlcampoSearchResponse
from app.scrapers.retry import send_with_retry
from app.services.rate_limiter import OutboundRateLimiter

SEARCH_PATH = "/api/webproductpagews/v6/product-pages/search"
PAGE_SIZE = 50

logger = logging.getLogger(__name__)


class AlcampoSearchScraper:
    """Searches Alcampo's product catalog by free text (RF-3)."""

    def __init__(self, *, settings: Settings, rate_limiter: OutboundRateLimiter) -> None:
        self._settings = settings
        self._rate_limiter = rate_limiter

    async def search(self, term: str, *, client: httpx.AsyncClient) -> AlcampoSearchResponse:
        """Return the raw, validated search envelope for `term` (a single page).

        `client` carries the cookies of a session confirmed in the wanted region:
        Alcampo takes the region from the session, not from the request (spec 007 RF-9).
        """
        params: dict[str, str | int] = {
            "q": term,
            "tag": "web",
            "maxPageSize": PAGE_SIZE,
            "maxProductsToDecorate": PAGE_SIZE,
        }
        # Relative URL for logs only (spec 003 plan-D7); contains the term, so it
        # is always logged with %r (RF-18).
        url = str(httpx.URL(SEARCH_PATH, params=params))

        async def send() -> httpx.Response:
            # One slot per real request, retries included (spec 008 RF-3). An
            # exhausted limit raises OutboundRateLimitedError, which
            # send_with_retry does not catch, so the retries stop (RF-5, plan-D4).
            await self._rate_limiter.acquire()
            return await client.get(SEARCH_PATH, params=params)

        response = await send_with_retry(
            send,
            max_attempts=self._settings.retry_max_attempts,
            base_delay=self._settings.retry_base_delay,
            jitter_max=self._settings.retry_jitter_max_s,
            url=url,
        )

        # The body is never logged: it is Alcampo's, not ours (spec 003 RF-17).
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            logger.error("invalid JSON from Alcampo url=%r", url)
            raise UpstreamUnavailableError("unexpected search response shape") from exc
        try:
            return AlcampoSearchResponse.model_validate(body)
        except ValidationError as exc:
            logger.error("unexpected schema from Alcampo url=%r", url)
            raise UpstreamUnavailableError("unexpected search response shape") from exc
