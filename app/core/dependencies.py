"""FastAPI dependency providers, centralized as required by the constitution.

`AppState` is a typed replacement for the loose attributes set on `app.state`
in the `lifespan`; a fully typed `state.AppState` arrives in spec 006 (plan-D10).
"""

from fastapi import Request

from app.scrapers.alcampo_search import AlcampoSearchScraper
from app.services.product_service import ProductService
from app.services.search_cache import SearchCacheRepository


def get_product_service(request: Request) -> ProductService:
    """Build a `ProductService` from the resources created in the `lifespan`."""
    state = request.app.state
    scraper = AlcampoSearchScraper(client=state.http_client, settings=state.settings)
    cache = SearchCacheRepository(state.redis)
    return ProductService(scraper=scraper, cache=cache, settings=state.settings)
