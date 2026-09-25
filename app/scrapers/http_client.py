"""Factory for the shared `httpx.AsyncClient` used to talk to Alcampo.

Headers mimic a real Chrome browser on Windows (the same UA used, without
incident, during the live Fase 0 research). User-Agent blocking is NOT
verified (Fase 0 §5); rotation across a pool of real browsers arrives in
spec 002.
"""

import httpx

from app.core.config import Settings

REQUEST_TIMEOUT_SECONDS = 10.0
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)


def create_http_client(settings: Settings) -> httpx.AsyncClient:
    """Build the single `httpx.AsyncClient` used for the lifetime of the app."""
    return httpx.AsyncClient(
        base_url=settings.alcampo_base_url,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "es-ES,es;q=0.9",
        },
    )
