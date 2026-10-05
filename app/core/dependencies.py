"""FastAPI dependency providers, centralized as required by the constitution.

Everything stateful is built once in the `lifespan` (`app.main`) and read
through the typed `resources(app)` (spec 006 RF-1); a provider only assembles
the per-request objects around it.
"""

from fastapi import Request

from app.core.state import resources
from app.scrapers.alcampo_search import AlcampoSearchScraper
from app.services.product_service import ProductService
from app.services.search_cache import SearchCacheRepository


def get_product_service(request: Request) -> ProductService:
    """Build a `ProductService` from the resources created in the `lifespan`."""
    res = resources(request.app)
    return ProductService(
        scraper=AlcampoSearchScraper(settings=res.settings, gate=res.gate),
        cache=SearchCacheRepository(res.redis, circuit=res.redis_circuit),
        cooldown=res.cooldown,
        in_flight=res.in_flight,
        regions=res.region_service,
        sessions=res.region_sessions,
        traffic=res.gate.traffic,
        settings=res.settings,
    )
