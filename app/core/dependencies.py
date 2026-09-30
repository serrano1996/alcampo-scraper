"""FastAPI dependency providers, centralized as required by the constitution.

Everything stateful is built once in the `lifespan` (`app.main`); a provider
only assembles the per-request objects around it. A typed `AppState` for these
attributes arrives in spec 006.
"""

from fastapi import Request

from app.scrapers.alcampo_search import AlcampoSearchScraper
from app.services.product_service import ProductService
from app.services.search_cache import SearchCacheRepository


def get_product_service(request: Request) -> ProductService:
    """Build a `ProductService` from the resources created in the `lifespan`."""
    state = request.app.state
    return ProductService(
        scraper=AlcampoSearchScraper(settings=state.settings, rate_limiter=state.rate_limiter),
        cache=SearchCacheRepository(state.redis, circuit=state.redis_circuit),
        cooldown=state.cooldown,
        in_flight=state.in_flight,
        regions=state.region_service,
        sessions=state.region_sessions,
        settings=state.settings,
    )
