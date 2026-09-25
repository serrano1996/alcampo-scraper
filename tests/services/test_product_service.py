from datetime import UTC, datetime

import fakeredis
import pytest

from app.core.config import Settings
from app.models.alcampo import AlcampoSearchResponse
from app.models.product import ProductQuery
from app.services.product_service import ProductService
from app.services.search_cache import SearchCacheRepository

FIXED_NOW = datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC)

RAW_PRODUCT = {
    "retailerProductId": "54180",
    "name": "AUCHAN Leche semidesnatada de vaca 6 x 1l Producto Alcampo.",
    "price": {"amount": "5.28", "currency": "EUR"},
    "unitPrice": {"price": {"amount": "0.88", "currency": "EUR"}, "unitName": "PER_LITRE"},
    "categoryPath": ["Leche, Huevos, Lácteos", "Leche", "Leche semidesnatada"],
}


class FakeScraper:
    def __init__(self, response: AlcampoSearchResponse) -> None:
        self.response = response
        self.calls: list[str] = []

    async def search(self, term: str) -> AlcampoSearchResponse:
        self.calls.append(term)
        return self.response


def make_raw_response(*, has_products: bool) -> AlcampoSearchResponse:
    products = [RAW_PRODUCT] if has_products else []
    return AlcampoSearchResponse.model_validate(
        {"productGroups": [{"decoratedProducts": products}]}
    )


def make_service(
    scraper: FakeScraper, redis: fakeredis.FakeAsyncRedis
) -> tuple[ProductService, SearchCacheRepository]:
    settings = Settings(
        _env_file=None,
        alcampo_base_url="https://alcampo.test",
        redis_url="redis://localhost:6379/0",
    )
    cache = SearchCacheRepository(redis)
    service = ProductService(
        scraper=scraper, cache=cache, settings=settings, clock=lambda: FIXED_NOW
    )
    return service, cache


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


async def test_miss_calls_the_scraper_once(redis: fakeredis.FakeAsyncRedis) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)

    await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert scraper.calls == ["leche"]


async def test_miss_returns_expected_metadata(redis: fakeredis.FakeAsyncRedis) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)

    result = await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert result.search.warehouse == "5"
    assert result.search.strategy_used == "api"
    assert result.search.scraped_at == FIXED_NOW
    assert result.search.total_results == 1
    assert result.search.postal_code == "28001"
    assert result.search.term == "leche"
    assert len(result.products) == 1


async def test_miss_stores_the_response_in_cache(redis: fakeredis.FakeAsyncRedis) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, cache = make_service(scraper, redis)

    await service.search(ProductQuery(postal_code="28001", term="leche"))

    cached = await cache.get(warehouse="5", term="leche")
    assert cached is not None
    assert cached.search.total_results == 1


async def test_empty_search_returns_empty_products_and_is_cached(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scraper = FakeScraper(make_raw_response(has_products=False))
    service, cache = make_service(scraper, redis)

    result = await service.search(ProductQuery(postal_code="28001", term="xqzwvkjhgf"))

    assert result.products == []
    assert result.search.total_results == 0
    cached = await cache.get(warehouse="5", term="xqzwvkjhgf")
    assert cached is not None
