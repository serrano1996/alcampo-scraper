import logging

import fakeredis
import pytest

from app.services.waf_cooldown import (
    WAF_COOLDOWN_KEY,
    WAF_COOLDOWN_LAST_KEY,
    LocalCooldown,
    WafCooldownRepository,
)
from tests.services.redis_doubles import DOWN, HUNG, BrokenRedis

BASE = 180
MAX = 900


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


async def activate(repo: WafCooldownRepository, base: int = BASE, maximum: int = MAX) -> int:
    return await repo.activate(base_seconds=base, max_seconds=maximum)


async def test_inactive_without_marker(redis: fakeredis.FakeAsyncRedis) -> None:
    assert await WafCooldownRepository(redis).is_active() is False


async def test_first_challenge_sets_a_global_marker_with_the_base_ttl(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    repo = WafCooldownRepository(redis)

    applied = await activate(repo)

    assert applied == BASE
    assert WAF_COOLDOWN_KEY == "waf:cooldown"
    assert await repo.is_active() is True
    assert BASE - 1 <= await redis.ttl(WAF_COOLDOWN_KEY) <= BASE


async def test_first_challenge_remembers_its_duration_for_the_max_window(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    # "Recent" means within WAF_COOLDOWN_MAX_SECONDS (spec 008 RF-8, plan-D7).
    await activate(WafCooldownRepository(redis))

    assert WAF_COOLDOWN_LAST_KEY == "waf:cooldown:last"
    assert await redis.get(WAF_COOLDOWN_LAST_KEY) == b"180"
    assert MAX - 1 <= await redis.ttl(WAF_COOLDOWN_LAST_KEY) <= MAX


async def test_recent_second_challenge_doubles_the_cooldown(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    # Replaces spec 002's "activate renews the TTL" (180 then 60): a new
    # challenge now doubles the previous duration instead (spec 008 RF-8).
    repo = WafCooldownRepository(redis)
    await activate(repo)

    applied = await activate(repo)

    assert applied == 360
    assert 359 <= await redis.ttl(WAF_COOLDOWN_KEY) <= 360
    assert await redis.get(WAF_COOLDOWN_LAST_KEY) == b"360"


async def test_consecutive_challenges_grow_up_to_the_cap(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    repo = WafCooldownRepository(redis)

    applied = [await activate(repo) for _ in range(5)]

    assert applied == [180, 360, 720, 900, 900]


async def test_challenge_after_the_window_starts_again_from_the_base(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    repo = WafCooldownRepository(redis)
    await activate(repo)
    await activate(repo)

    await redis.delete(WAF_COOLDOWN_KEY, WAF_COOLDOWN_LAST_KEY)  # both TTLs expired

    assert await activate(repo) == BASE


async def test_zero_base_disables_the_cooldown(redis: fakeredis.FakeAsyncRedis) -> None:
    repo = WafCooldownRepository(redis)

    applied = await activate(repo, base=0)

    assert applied == 0
    assert await redis.keys("*") == []
    assert await repo.is_active() is False


async def test_inactive_once_the_marker_expires(redis: fakeredis.FakeAsyncRedis) -> None:
    repo = WafCooldownRepository(redis)
    await activate(repo)

    await redis.delete(WAF_COOLDOWN_KEY)  # simulates TTL expiry

    assert await repo.is_active() is False


# --- spec 007: local fallback without Redis ------------------------------------


class FakeMonotonic:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize("error", [DOWN, HUNG], ids=["down", "hung"])
async def test_without_redis_the_local_cooldown_grows_the_same_way(
    caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    repo = WafCooldownRepository(BrokenRedis(error), fallback=LocalCooldown())

    applied = [await activate(repo) for _ in range(5)]

    assert applied == [180, 360, 720, 900, 900]
    assert await repo.is_active() is True
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings
    assert all("redis unavailable" in r.getMessage() for r in warnings)


async def test_local_cooldown_expires_and_forgets_old_challenges() -> None:
    clock = FakeMonotonic()
    repo = WafCooldownRepository(BrokenRedis(DOWN), fallback=LocalCooldown(now=clock))
    assert await activate(repo) == 180

    clock.now += 181
    assert await repo.is_active() is False
    assert await activate(repo) == 360  # 181 s later: still a recent challenge

    clock.now += 901
    assert await activate(repo) == 180  # more than MAX later: from the base again


async def test_local_cooldown_with_zero_base_stays_disabled() -> None:
    repo = WafCooldownRepository(BrokenRedis(DOWN), fallback=LocalCooldown())

    assert await activate(repo, base=0) == 0
    assert await repo.is_active() is False
