import logging

import fakeredis
import pytest

from app.services.region_repository import (
    NOT_SERVED,
    Region,
    RegionMemory,
    RegionRepository,
)
from tests.services.redis_doubles import DOWN, HUNG, BrokenRedis

TTL = 604800
NEGATIVE_TTL = 3600
TELDE = Region(
    region_id="c98744f2-ca04-4583-bbfc-c52f24548329",
    retailer_region_id="32",
    delivery_destination_id="44444444-4444-4444-8444-444444444444",
)


class FakeMonotonic:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


def make_repo(redis: object, memory: RegionMemory | None = None) -> RegionRepository:
    return RegionRepository(
        redis,
        memory=memory or RegionMemory(),
        ttl_seconds=TTL,
        negative_ttl_seconds=NEGATIVE_TTL,
    )


async def test_postal_code_region_is_stored_for_seven_days(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    repo = make_repo(redis)

    await repo.save_postal_code("35001", TELDE.region_id)

    assert await make_repo(redis).region_id_for("35001") == TELDE.region_id
    assert TTL - 1 <= await redis.ttl("postal-code-region:35001") <= TTL


async def test_not_served_postal_code_is_stored_for_one_hour(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await make_repo(redis).save_not_served("51001")

    assert await make_repo(redis).region_id_for("51001") == NOT_SERVED
    assert NEGATIVE_TTL - 1 <= await redis.ttl("postal-code-region:51001") <= NEGATIVE_TTL


async def test_unknown_postal_code_has_no_region(redis: fakeredis.FakeAsyncRedis) -> None:
    assert await make_repo(redis).region_id_for("28001") is None


async def test_region_record_keeps_its_retailer_id_and_destination(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    await make_repo(redis).save_region(TELDE)

    assert await make_repo(redis).region(TELDE.region_id) == TELDE
    assert TTL - 1 <= await redis.ttl(f"region:{TELDE.region_id}") <= TTL


async def test_memory_answers_without_touching_redis(redis: fakeredis.FakeAsyncRedis) -> None:
    # The repository is built per request: the memory lives in app.state.
    memory = RegionMemory()
    await make_repo(redis, memory).save_postal_code("35001", TELDE.region_id)
    await make_repo(redis, memory).save_region(TELDE)
    await redis.flushall()  # only the memory can answer now

    repo = make_repo(redis, memory)

    assert await repo.region_id_for("35001") == TELDE.region_id
    assert await repo.region(TELDE.region_id) == TELDE


async def test_a_redis_read_fills_the_memory(redis: fakeredis.FakeAsyncRedis) -> None:
    memory = RegionMemory()
    await make_repo(redis).save_postal_code("35001", TELDE.region_id)  # another process
    assert await make_repo(redis, memory).region_id_for("35001") == TELDE.region_id
    await redis.flushall()

    assert await make_repo(redis, memory).region_id_for("35001") == TELDE.region_id


async def test_memory_entries_expire_with_their_ttl(redis: fakeredis.FakeAsyncRedis) -> None:
    clock = FakeMonotonic()
    memory = RegionMemory(now=clock)
    await make_repo(redis, memory).save_not_served("51001")
    await redis.flushall()

    clock.now += NEGATIVE_TTL + 1

    assert await make_repo(redis, memory).region_id_for("51001") is None


@pytest.mark.parametrize("error", [DOWN, HUNG], ids=["down", "hung"])
async def test_without_redis_only_the_memory_is_used(
    caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    repo = make_repo(BrokenRedis(error))

    await repo.save_postal_code("35001", TELDE.region_id)
    await repo.save_region(TELDE)

    assert await repo.region_id_for("35001") == TELDE.region_id
    assert await repo.region(TELDE.region_id) == TELDE
    assert await repo.region_id_for("28001") is None
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings
    assert all("redis unavailable" in message for message in warnings)


async def test_forget_region_removes_it_everywhere(redis: fakeredis.FakeAsyncRedis) -> None:
    memory = RegionMemory()
    repo = make_repo(redis, memory)
    await repo.save_region(TELDE)

    await repo.forget_region(TELDE.region_id)

    assert await repo.region(TELDE.region_id) is None
    assert await redis.exists(f"region:{TELDE.region_id}") == 0


async def test_corrupted_region_record_is_treated_as_missing(
    redis: fakeredis.FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    await redis.set(f"region:{TELDE.region_id}", "not json")

    assert await make_repo(redis).region(TELDE.region_id) is None
    assert any("corrupted region record" in r.getMessage() for r in caplog.records)
