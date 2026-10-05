"""Spec 007 RF-18: stop trying Redis for a while after a failure."""

import logging
from datetime import UTC, datetime

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError

from app.models.product import ProductSearchResponse, SearchMetadata
from app.services.rate_limiter import LocalRateLimiter, OutboundRateLimiter
from app.services.redis_circuit import RedisCircuitBreaker, RedisCircuitOpenError
from app.services.region_repository import RegionMemory, RegionRepository
from app.services.search_cache import SearchCacheRepository
from app.services.waf_cooldown import LocalCooldown, WafCooldownRepository
from tests.services.redis_doubles import DOWN, CountingBrokenRedis

OPEN = 10


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class Operation:
    """An async Redis operation that fails or succeeds on demand, counting calls."""

    def __init__(self, *, fails: bool) -> None:
        self.fails = fails
        self.calls = 0

    async def __call__(self) -> str:
        self.calls += 1
        if self.fails:
            raise RedisConnectionError("Connection refused")
        return "ok"


def records(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "app.services.redis_circuit" and r.levelno == level
    ]


def test_the_open_error_is_a_redis_error() -> None:
    # Repositories already fall back on RedisError: nothing else to change there.
    assert issubclass(RedisCircuitOpenError, RedisError)


async def test_a_closed_circuit_runs_the_operation() -> None:
    breaker = RedisCircuitBreaker(open_seconds=OPEN, now=FakeClock())

    assert await breaker.call(Operation(fails=False)) == "ok"


async def test_a_failure_opens_the_circuit_once_and_is_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    breaker = RedisCircuitBreaker(open_seconds=OPEN, now=FakeClock())

    with pytest.raises(RedisConnectionError):
        await breaker.call(Operation(fails=True))

    [warning] = records(caplog, logging.WARNING)
    assert "redis circuit open" in warning
    assert "10" in warning


async def test_an_open_circuit_skips_redis_without_waiting() -> None:
    breaker = RedisCircuitBreaker(open_seconds=OPEN, now=FakeClock())
    with pytest.raises(RedisError):
        await breaker.call(Operation(fails=True))
    operation = Operation(fails=False)

    with pytest.raises(RedisCircuitOpenError):
        await breaker.call(operation)

    assert operation.calls == 0


async def test_after_the_period_a_success_closes_the_circuit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    clock = FakeClock()
    breaker = RedisCircuitBreaker(open_seconds=OPEN, now=clock)
    with pytest.raises(RedisError):
        await breaker.call(Operation(fails=True))

    clock.now += OPEN + 1
    assert await breaker.call(Operation(fails=False)) == "ok"
    assert await breaker.call(Operation(fails=False)) == "ok"  # closed again

    assert any("redis circuit closed" in m for m in records(caplog, logging.INFO))


async def test_after_the_period_a_failure_opens_it_again() -> None:
    clock = FakeClock()
    breaker = RedisCircuitBreaker(open_seconds=OPEN, now=clock)
    with pytest.raises(RedisError):
        await breaker.call(Operation(fails=True))
    clock.now += OPEN + 1

    with pytest.raises(RedisConnectionError):
        await breaker.call(Operation(fails=True))  # the probe fails

    operation = Operation(fails=False)
    with pytest.raises(RedisCircuitOpenError):
        await breaker.call(operation)
    assert operation.calls == 0


async def test_zero_seconds_disables_the_circuit() -> None:
    breaker = RedisCircuitBreaker(open_seconds=0, now=FakeClock())
    for _ in range(3):
        with pytest.raises(RedisConnectionError):
            await breaker.call(Operation(fails=True))

    assert await breaker.call(Operation(fails=False)) == "ok"


# --- every Redis-backed repository goes through it ----------------------------


async def opened_breaker() -> RedisCircuitBreaker:
    breaker = RedisCircuitBreaker(open_seconds=OPEN, now=FakeClock())
    with pytest.raises(RedisError):
        await breaker.call(Operation(fails=True))
    return breaker


def skipped_quietly(caplog: pytest.LogCaptureFixture) -> bool:
    """While open, repositories say so in DEBUG only: one WARNING per incident."""
    repo_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and r.name != "app.services.redis_circuit"
    ]
    return repo_warnings == []


CACHED = ProductSearchResponse(
    search=SearchMetadata(
        postal_code="28001",
        term="leche",
        warehouse="5",
        strategy_used="api",
        scraped_at=datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC),
        total_results=0,
        page=1,
        page_size=50,
        total_pages=0,
    ),
    products=[],
)


async def test_the_search_cache_skips_redis_while_open(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = CountingBrokenRedis(DOWN)
    repo = SearchCacheRepository(redis, circuit=await opened_breaker())

    assert await repo.get(warehouse="5", term="leche") is None
    await repo.set(warehouse="5", term="leche", response=CACHED, ttl_seconds=60)

    assert redis.attempts == 0
    assert skipped_quietly(caplog)


async def test_the_rate_limiter_uses_its_fallback_while_open(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = CountingBrokenRedis(DOWN)
    limiter = OutboundRateLimiter(
        redis,
        limit=5,
        window_seconds=60,
        fallback=LocalRateLimiter(),
        circuit=await opened_breaker(),
    )

    await limiter.acquire()

    assert redis.attempts == 0
    assert skipped_quietly(caplog)


async def test_the_cooldown_uses_its_fallback_while_open(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = CountingBrokenRedis(DOWN)
    repo = WafCooldownRepository(redis, fallback=LocalCooldown(), circuit=await opened_breaker())

    assert await repo.activate(base_seconds=180, max_seconds=900) == 180
    assert await repo.is_active() is True

    assert redis.attempts == 0
    assert skipped_quietly(caplog)


async def test_the_region_repository_uses_its_memory_while_open(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = CountingBrokenRedis(DOWN)
    repo = RegionRepository(
        redis,
        memory=RegionMemory(),
        ttl_seconds=60,
        negative_ttl_seconds=60,
        circuit=await opened_breaker(),
    )

    await repo.save_postal_code("35001", "telde")
    assert await repo.region_id_for("35001") == "telde"
    assert await repo.region_id_for("28001") is None
    await repo.forget_region("telde")

    assert redis.attempts == 0
    assert skipped_quietly(caplog)
