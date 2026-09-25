"""Factory for the shared `httpx.AsyncClient` used to talk to Alcampo.

The User-Agent is picked at random from a pool of real browsers, once per
client instance (spec 002 RF-1): rotating it within the same session, which
shares Alcampo's VISITORID/AWSALB cookies, would be a stronger bot signal
than keeping it fixed.

Pool versions verified on 2026-09-25 against the official release APIs of
each browser (spec 002, D1). Review roughly every 3 months: an outdated
User-Agent is itself a bot signal.
"""

import random
from collections.abc import Callable, Sequence

import httpx

from app.core.config import Settings

REQUEST_TIMEOUT_SECONDS = 10.0

# Constant on purpose (plan-D11): it imitates the real browser, not our config.
# Deriving it from ALCAMPO_BASE_URL would leak a fake Referer if the base URL
# pointed to a proxy. No `Origin`: browsers do not send it on same-origin GETs.
ALCAMPO_REFERER = "https://www.compraonline.alcampo.es/"

USER_AGENTS: tuple[str, ...] = (
    # Chrome 155 / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
    # Chrome 155 / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
    # Chrome 154 / Linux (Linux stable lags one version behind)
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    # Firefox 156 / Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0",
    # Edge 154 / Windows (the real Edge UA includes the Chrome/ token)
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0",
    # Safari 27 / macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/27.0 Safari/605.1.15",
)

ChooseUserAgent = Callable[[Sequence[str]], str]


def create_http_client(
    settings: Settings, *, choose: ChooseUserAgent = random.choice
) -> httpx.AsyncClient:
    """Build the single `httpx.AsyncClient` used for the lifetime of the app."""
    return httpx.AsyncClient(
        base_url=settings.alcampo_base_url,
        timeout=REQUEST_TIMEOUT_SECONDS,
        headers={
            "User-Agent": choose(USER_AGENTS),
            "Accept": "application/json",
            "Accept-Language": "es-ES,es;q=0.9",
            "Referer": ALCAMPO_REFERER,
            # Sent by Alcampo's own web client on every API call (Fase 0, bundle).
            "ecom-request-source": "web",
        },
    )
