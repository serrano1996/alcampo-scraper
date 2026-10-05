"""Client for Alcampo's product search endpoint (webproductpagews v6)."""

import json
import logging

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.exceptions import UpstreamUnavailableError
from app.models.alcampo import AlcampoSearchResponse
from app.scrapers.retry import send_with_retry
from app.services.outbound import OutboundGate

SEARCH_PATH = "/api/webproductpagews/v6/product-pages/search"
PAGE_SIZE = 50
# Alcampo's own web client sends at most 50 characters (Fase 0 §1, spec 009 spec-D6).
MAX_SENT_TERM_LENGTH = 50

logger = logging.getLogger(__name__)


class AlcampoSearchScraper:
    """Searches Alcampo's product catalog by free text (RF-3)."""

    def __init__(self, *, settings: Settings, gate: OutboundGate) -> None:
        self._settings = settings
        self._gate = gate

    async def search(
        self,
        term: str,
        *,
        client: httpx.AsyncClient,
        page_size: int = PAGE_SIZE,
        page_token: str | None = None,
    ) -> AlcampoSearchResponse:
        """Return the raw, validated search envelope for one page of `term`.

        `client` carries the cookies of a session confirmed in the wanted region:
        Alcampo takes the region from the session, not from the request (spec 007 RF-9).
        `page_token` is the previous page's `nextPageToken`, valid only in that
        same session (spec 009 RF-7); the first page goes without one.
        """
        params: dict[str, str | int] = {
            "q": term,
            "tag": "web",
            "maxPageSize": page_size,
            "maxProductsToDecorate": page_size,
        }
        if page_token is not None:
            params["pageToken"] = page_token
        # Relative URL for logs only (spec 003 plan-D7); contains the term, so it
        # is always logged with %r (RF-18).
        url = str(httpx.URL(SEARCH_PATH, params=params))

        async def send() -> httpx.Response:
            # Every real request, retries included, goes through the outbound gate:
            # spacing and both windows (spec 010 plan-D1, spec 008 RF-3). An
            # exhausted limit raises OutboundRateLimitedError, which
            # send_with_retry does not catch, so the retries stop (spec 008 RF-5).
            await self._gate.before_request("search")
            response = await client.get(SEARCH_PATH, params=params)
            self._gate.after_response(
                kind="search", endpoint=SEARCH_PATH, status=response.status_code
            )
            return response

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
