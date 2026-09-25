import fakeredis
import pytest

from app.services.waf_cooldown import WAF_COOLDOWN_KEY, WafCooldownRepository


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


async def test_inactive_without_marker(redis: fakeredis.FakeAsyncRedis) -> None:
    assert await WafCooldownRepository(redis).is_active() is False


async def test_activate_sets_a_global_marker_with_ttl(redis: fakeredis.FakeAsyncRedis) -> None:
    repo = WafCooldownRepository(redis)

    await repo.activate(ttl_seconds=180)

    assert WAF_COOLDOWN_KEY == "waf:cooldown"
    assert await repo.is_active() is True
    assert 179 <= await redis.ttl(WAF_COOLDOWN_KEY) <= 180


async def test_activate_with_zero_ttl_disables_the_cooldown(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    repo = WafCooldownRepository(redis)

    await repo.activate(ttl_seconds=0)

    assert await redis.exists(WAF_COOLDOWN_KEY) == 0
    assert await repo.is_active() is False


async def test_inactive_once_the_marker_expires(redis: fakeredis.FakeAsyncRedis) -> None:
    repo = WafCooldownRepository(redis)
    await repo.activate(ttl_seconds=180)

    await redis.delete(WAF_COOLDOWN_KEY)  # simulates TTL expiry

    assert await repo.is_active() is False


async def test_activate_renews_the_ttl(redis: fakeredis.FakeAsyncRedis) -> None:
    repo = WafCooldownRepository(redis)
    await repo.activate(ttl_seconds=180)

    await repo.activate(ttl_seconds=60)

    assert 59 <= await redis.ttl(WAF_COOLDOWN_KEY) <= 60
