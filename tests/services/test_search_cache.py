import logging
from datetime import UTC, datetime

import fakeredis
import pytest

from app.models.product import Product, ProductSearchResponse, SearchMetadata
from app.services.search_cache import SearchCacheRepository, normalize_term
from tests.services.redis_doubles import DOWN, HUNG, BrokenRedis


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


def make_response(postal_code: str = "28001") -> ProductSearchResponse:
    return ProductSearchResponse(
        search=SearchMetadata(
            postal_code=postal_code,
            term="leche",
            warehouse="5",
            strategy_used="api",
            scraped_at=datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC),
            total_results=1,
            page=1,
            page_size=50,
            total_pages=1,
        ),
        products=[
            Product(
                id="54180",
                name="Leche",
                price=5.28,
                price_format="0.88 €/L",
                image_url="https://img.test/54180.jpg",
                category="Leche semidesnatada",
            )
        ],
    )


async def test_get_on_empty_key_returns_none(redis: fakeredis.FakeAsyncRedis) -> None:
    repo = SearchCacheRepository(redis)

    result = await repo.get(warehouse="5", term="leche", page=1, page_size=50)

    assert result is None


async def test_set_then_get_returns_the_same_response(redis: fakeredis.FakeAsyncRedis) -> None:
    repo = SearchCacheRepository(redis)
    response = make_response()

    await repo.set(
        warehouse="5", term="leche", page=1, page_size=50, response=response, ttl_seconds=3600
    )
    result = await repo.get(warehouse="5", term="leche", page=1, page_size=50)

    assert result == response


async def test_key_is_search_warehouse_term_page_and_size(redis: fakeredis.FakeAsyncRedis) -> None:
    repo = SearchCacheRepository(redis)

    await repo.set(
        warehouse="5",
        term="leche",
        page=1,
        page_size=50,
        response=make_response(),
        ttl_seconds=3600,
    )

    assert await redis.exists("search:5:leche:1:50")


async def test_set_applies_the_given_ttl(redis: fakeredis.FakeAsyncRedis) -> None:
    repo = SearchCacheRepository(redis)

    await repo.set(
        warehouse="5",
        term="leche",
        page=1,
        page_size=50,
        response=make_response(),
        ttl_seconds=3600,
    )

    ttl = await redis.ttl("search:5:leche:1:50")
    assert 3595 <= ttl <= 3600


async def test_corrupted_value_is_treated_as_a_miss(redis: fakeredis.FakeAsyncRedis) -> None:
    repo = SearchCacheRepository(redis)
    await redis.set("search:5:leche:1:50", "not json")

    result = await repo.get(warehouse="5", term="leche", page=1, page_size=50)

    assert result is None


async def test_corrupted_value_logs_a_warning_with_the_key(
    redis: fakeredis.FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    await redis.set("search:5:leche:1:50", "not json")

    await SearchCacheRepository(redis).get(warehouse="5", term="leche", page=1, page_size=50)

    [record] = [r for r in caplog.records if r.name == "app.services.search_cache"]
    assert record.levelno == logging.WARNING
    assert "'search:5:leche:1:50'" in record.getMessage()


# --- spec 008 RF-2: normalized term -------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Leche", "leche"),
        ("LECHE", "leche"),
        ("leche   entera", "leche entera"),
        ("leche	entera", "leche entera"),
        (" leche ", "leche"),
    ],
)
def test_normalize_term(raw: str, expected: str) -> None:
    assert normalize_term(raw) == expected


async def test_case_and_spacing_variants_share_one_entry(redis: fakeredis.FakeAsyncRedis) -> None:
    repo = SearchCacheRepository(redis)
    response = make_response()

    await repo.set(
        warehouse="5", term="Leche", page=1, page_size=50, response=response, ttl_seconds=3600
    )

    assert await repo.get(warehouse="5", term="LECHE", page=1, page_size=50) == response
    assert await redis.keys("search:*") == [b"search:5:leche:1:50"]


# --- spec 007 RF-15: the cache is skipped without Redis ------------------------


@pytest.mark.parametrize("error", [DOWN, HUNG], ids=["down", "hung"])
async def test_without_redis_get_is_a_miss_and_set_is_skipped(
    caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    repo = SearchCacheRepository(BrokenRedis(error))

    assert await repo.get(warehouse="5", term="leche", page=1, page_size=50) is None
    await repo.set(
        warehouse="5",
        term="leche",
        page=1,
        page_size=50,
        response=make_response(),
        ttl_seconds=3600,
    )

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2
    assert all("redis unavailable" in message for message in warnings)


# --- spec 009 RF-9: one entry per page and page size ---------------------------


async def test_each_page_and_page_size_has_its_own_entry(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    repo = SearchCacheRepository(redis)
    response = make_response()

    await repo.set(
        warehouse="5", term="leche", page=2, page_size=10, response=response, ttl_seconds=3600
    )

    assert await repo.get(warehouse="5", term="leche", page=2, page_size=10) == response
    assert await repo.get(warehouse="5", term="leche", page=1, page_size=10) is None
    assert await repo.get(warehouse="5", term="leche", page=2, page_size=50) is None
    assert await redis.keys("search:*") == [b"search:5:leche:2:10"]
