import fakeredis
import pytest

from app.services.waf_cooldown import (
    WAF_COOLDOWN_KEY,
    WAF_COOLDOWN_LAST_KEY,
    WafCooldownRepository,
)

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
