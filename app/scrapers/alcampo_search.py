"""Client for Alcampo's product search endpoint (webproductpagews v6)."""

import json

import httpx
from pydantic import ValidationError

from app.core.config import Settings
from app.exceptions import UpstreamUnavailableError
from app.models.alcampo import AlcampoSearchResponse
from app.scrapers.retry import send_with_retry

SEARCH_PATH = "/api/webproductpagews/v6/product-pages/search"
PAGE_SIZE = 50

# Verified live in Fase 0 (2026-09-24): retailerRegionId of the anonymous
# session's default region ("Vaguada", Madrid). See docs/investigacion/
# fase-0-alcampo.md and spec-D1. Real localization arrives in spec 007.
DEFAULT_WAREHOUSE = "5"


class AlcampoSearchScraper:
    """Searches Alcampo's product catalog by free text (RF-3)."""

    def __init__(self, *, client: httpx.AsyncClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    async def search(self, term: str) -> AlcampoSearchResponse:
        """Return the raw, validated search envelope for `term` (a single page)."""

        async def send() -> httpx.Response:
            return await self._client.get(
                SEARCH_PATH,
                params={
                    "q": term,
                    "tag": "web",
                    "maxPageSize": PAGE_SIZE,
                    "maxProductsToDecorate": PAGE_SIZE,
                },
            )

        response = await send_with_retry(
            send,
            max_attempts=self._settings.retry_max_attempts,
            base_delay=self._settings.retry_base_delay,
        )

        try:
            body = response.json()
            return AlcampoSearchResponse.model_validate(body)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise UpstreamUnavailableError("unexpected search response shape") from exc
