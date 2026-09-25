from collections.abc import Callable
from datetime import UTC, datetime

import fakeredis
import pytest

from app.core.config import Settings
from app.exceptions import UpstreamBlockedError, UpstreamUnavailableError
from app.models.alcampo import AlcampoSearchResponse
from app.models.product import ProductQuery
from app.services.product_service import ProductService
from app.services.search_cache import SearchCacheRepository
from app.services.waf_cooldown import WAF_COOLDOWN_KEY, WafCooldownRepository

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


class FailingScraper:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls: list[str] = []

    async def search(self, term: str) -> AlcampoSearchResponse:
        self.calls.append(term)
        raise self.error


def make_raw_response(*, has_products: bool) -> AlcampoSearchResponse:
    products = [RAW_PRODUCT] if has_products else []
    return AlcampoSearchResponse.model_validate(
        {"productGroups": [{"decoratedProducts": products}]}
    )


def make_service(
    scraper: FakeScraper | FailingScraper,
    redis: fakeredis.FakeAsyncRedis,
    *,
    clock: Callable[[], datetime] = lambda: FIXED_NOW,
) -> tuple[ProductService, SearchCacheRepository]:
    settings = Settings(
        _env_file=None,
        alcampo_base_url="https://alcampo.test",
        redis_url="redis://localhost:6379/0",
    )
    cache = SearchCacheRepository(redis)
    service = ProductService(
        scraper=scraper,
        cache=cache,
        cooldown=WafCooldownRepository(redis),
        settings=settings,
        clock=clock,
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


async def test_cache_hit_does_not_call_the_scraper(redis: fakeredis.FakeAsyncRedis) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)
    await service.search(ProductQuery(postal_code="28001", term="leche"))
    scraper.calls.clear()

    await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert scraper.calls == []


async def test_cache_hit_rewrites_the_requested_postal_code(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)
    await service.search(ProductQuery(postal_code="28001", term="leche"))

    result = await service.search(ProductQuery(postal_code="08001", term="leche"))

    assert result.search.postal_code == "08001"


async def test_cache_hit_keeps_the_original_scraped_at(redis: fakeredis.FakeAsyncRedis) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    clock_values = iter(
        [datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC), datetime(2026, 9, 24, 11, 0, 0, tzinfo=UTC)]
    )
    service, _ = make_service(scraper, redis, clock=lambda: next(clock_values))
    first = await service.search(ProductQuery(postal_code="28001", term="leche"))

    second = await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert second.search.scraped_at == first.search.scraped_at


async def test_scraper_error_propagates_and_nothing_is_cached(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scraper = FailingScraper(UpstreamUnavailableError("boom"))
    service, cache = make_service(scraper, redis)

    with pytest.raises(UpstreamUnavailableError):
        await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert await cache.get(warehouse="5", term="leche") is None


async def test_blocked_scraper_activates_the_cooldown_and_propagates(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    service, _ = make_service(FailingScraper(UpstreamBlockedError("waf")), redis)

    with pytest.raises(UpstreamBlockedError):
        await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert await WafCooldownRepository(redis).is_active() is True
    assert 179 <= await redis.ttl(WAF_COOLDOWN_KEY) <= 180


async def test_plain_upstream_error_does_not_activate_the_cooldown(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    service, _ = make_service(FailingScraper(UpstreamUnavailableError("503")), redis)

    with pytest.raises(UpstreamUnavailableError):
        await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert await WafCooldownRepository(redis).is_active() is False
