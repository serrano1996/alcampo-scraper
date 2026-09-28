"""FastAPI dependency providers, centralized as required by the constitution.

`AppState` is a typed replacement for the loose attributes set on `app.state`
in the `lifespan`; a fully typed `state.AppState` arrives in spec 006 (plan-D10).
"""

from fastapi import Request

from app.scrapers.alcampo_search import AlcampoSearchScraper
from app.services.product_service import ProductService
from app.services.rate_limiter import OutboundRateLimiter
from app.services.search_cache import SearchCacheRepository
from app.services.waf_cooldown import WafCooldownRepository


def get_product_service(request: Request) -> ProductService:
    """Build a `ProductService` from the resources created in the `lifespan`."""
    state = request.app.state
    settings = state.settings
    rate_limiter = OutboundRateLimiter(
        state.redis,
        limit=settings.alcampo_rate_limit,
        window_seconds=settings.alcampo_rate_window_seconds,
    )
    scraper = AlcampoSearchScraper(
        client=state.http_client, settings=settings, rate_limiter=rate_limiter
    )
    cache = SearchCacheRepository(state.redis)
    return ProductService(
        scraper=scraper,
        cache=cache,
        cooldown=WafCooldownRepository(state.redis),
        in_flight=state.in_flight,
        settings=state.settings,
    )
