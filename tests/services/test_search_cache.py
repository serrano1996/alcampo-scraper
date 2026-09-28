import logging
from datetime import UTC, datetime

import fakeredis
import pytest

from app.models.product import Product, ProductSearchResponse, SearchMetadata
from app.services.search_cache import SearchCacheRepository, normalize_term


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
        ),
        products=[
            Product(
                id="54180",
                name="Leche",
                price=5.28,
                price_format="0.88 €/L",
                image_url=None,
                category=None,
            )
        ],
    )


async def test_get_on_empty_key_returns_none(redis: fakeredis.FakeAsyncRedis) -> None:
    repo = SearchCacheRepository(redis)

    result = await repo.get(warehouse="5", term="leche")

    assert result is None


async def test_set_then_get_returns_the_same_response(redis: fakeredis.FakeAsyncRedis) -> None:
    repo = SearchCacheRepository(redis)
    response = make_response()

    await repo.set(warehouse="5", term="leche", response=response, ttl_seconds=3600)
    result = await repo.get(warehouse="5", term="leche")

    assert result == response


async def test_key_is_search_warehouse_term(redis: fakeredis.FakeAsyncRedis) -> None:
    repo = SearchCacheRepository(redis)

    await repo.set(warehouse="5", term="leche", response=make_response(), ttl_seconds=3600)

    assert await redis.exists("search:5:leche")


async def test_set_applies_the_given_ttl(redis: fakeredis.FakeAsyncRedis) -> None:
    repo = SearchCacheRepository(redis)

    await repo.set(warehouse="5", term="leche", response=make_response(), ttl_seconds=3600)

    ttl = await redis.ttl("search:5:leche")
    assert 3595 <= ttl <= 3600


async def test_corrupted_value_is_treated_as_a_miss(redis: fakeredis.FakeAsyncRedis) -> None:
    repo = SearchCacheRepository(redis)
    await redis.set("search:5:leche", "not json")

    result = await repo.get(warehouse="5", term="leche")

    assert result is None


async def test_corrupted_value_logs_a_warning_with_the_key(
    redis: fakeredis.FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    await redis.set("search:5:leche", "not json")

    await SearchCacheRepository(redis).get(warehouse="5", term="leche")

    [record] = [r for r in caplog.records if r.name == "app.services.search_cache"]
    assert record.levelno == logging.WARNING
    assert "'search:5:leche'" in record.getMessage()


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

    await repo.set(warehouse="5", term="Leche", response=response, ttl_seconds=3600)

    assert await repo.get(warehouse="5", term="LECHE") == response
    assert await redis.keys("search:*") == [b"search:5:leche"]
