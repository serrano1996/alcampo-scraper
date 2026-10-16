import asyncio
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
    await redis.delete(WAF_COOLDOWN_KEY)  # over, but recent (spec 016 RF-1)

    applied = await activate(repo)

    assert applied == 360
    assert 359 <= await redis.ttl(WAF_COOLDOWN_KEY) <= 360
    assert await redis.get(WAF_COOLDOWN_LAST_KEY) == b"360"


async def test_consecutive_challenges_grow_up_to_the_cap(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    repo = WafCooldownRepository(redis)
    applied = []
    for _ in range(5):
        applied.append(await activate(repo))
        await redis.delete(WAF_COOLDOWN_KEY)  # each one over, the last still recent

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
    clock = FakeMonotonic()
    repo = WafCooldownRepository(BrokenRedis(error), fallback=LocalCooldown(now=clock))
    applied = []
    for _ in range(4):
        applied.append(await activate(repo))
        clock.now += applied[-1]  # each one over (spec 016 RF-1), the last still recent

    # The 900 is over 900 s later: no longer recent, so the local one never repeats it.
    assert applied == [180, 360, 720, 900]
    assert await activate(repo) == 180
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


# --- Spec 015 RF-9: a cooldown started in Redis survives Redis dying (F9, plan-D8) ---


class SwitchableRedis:
    """A fakeredis that can go down."""

    def __init__(self, server: fakeredis.FakeServer | None = None) -> None:
        self.fake = fakeredis.FakeAsyncRedis(server=server)
        self.down = False

    def __getattr__(self, name: str) -> object:
        if self.down:
            raise DOWN
        return getattr(self.fake, name)


async def test_a_cooldown_started_in_redis_holds_if_redis_dies() -> None:
    # Before: only Redis knew; with Redis gone the instance called Alcampo again.
    clock = FakeMonotonic()
    redis = SwitchableRedis()
    repo = WafCooldownRepository(redis, fallback=LocalCooldown(now=clock))
    assert await activate(repo) == BASE

    redis.down = True
    clock.now += BASE - 1

    assert await repo.is_active() is True
    clock.now += 1
    assert await repo.is_active() is False


async def test_with_redis_gone_the_growth_continues_from_redis(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeMonotonic()
    redis = SwitchableRedis()
    repo = WafCooldownRepository(redis, fallback=LocalCooldown(now=clock))
    assert await activate(repo) == 180
    await redis.fake.delete(WAF_COOLDOWN_KEY)
    clock.now += 180
    assert await activate(repo) == 360

    redis.down = True
    clock.now += 360

    assert await activate(repo) == 720  # not back to 180


async def test_a_challenge_during_the_cooldown_neither_grows_nor_extends_it(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    # Spec 016 RF-1 (F1): a request sent before the cooldown that gets challenged
    # during it confirms the same block; it used to double it (180 -> 360).
    repo = WafCooldownRepository(redis)
    await activate(repo)

    applied = await activate(repo)

    assert BASE - 1 <= applied <= BASE
    assert await redis.get(WAF_COOLDOWN_LAST_KEY) == b"180"
    assert await redis.ttl(WAF_COOLDOWN_KEY) <= BASE


async def test_without_redis_a_challenge_during_the_local_cooldown_does_not_grow_it() -> None:
    clock = FakeMonotonic()
    repo = WafCooldownRepository(BrokenRedis(DOWN), fallback=LocalCooldown(now=clock))
    assert await activate(repo) == BASE

    clock.now += 60
    assert await activate(repo) == BASE - 60  # what is left, the end unchanged

    clock.now += BASE - 60
    assert await repo.is_active() is False
    assert await activate(repo) == 360  # grows from 180, not from 120


async def test_a_challenge_during_a_redis_cooldown_keeps_the_local_mirror_intact() -> None:
    # Mirroring "what is left" as the last duration would make the local growth
    # start from less than Redis' if Redis died afterwards.
    clock = FakeMonotonic()
    redis = SwitchableRedis()
    repo = WafCooldownRepository(redis, fallback=LocalCooldown(now=clock))
    await activate(repo)
    await activate(repo)

    redis.down = True
    clock.now += BASE

    assert await repo.is_active() is False
    assert await activate(repo) == 360


async def test_joining_another_instance_cooldown_still_arms_the_local_one() -> None:
    # Spec 015 RF-9 with two instances (spec 016 review B1): B's challenge joins
    # A's cooldown; if Redis then dies, B must still hold it, and keep growing
    # from Redis' last duration, not from what was left.
    server = fakeredis.FakeServer()
    clock_a, clock_b = FakeMonotonic(), FakeMonotonic()
    redis_b = SwitchableRedis(server)
    a = WafCooldownRepository(SwitchableRedis(server), fallback=LocalCooldown(now=clock_a))
    b = WafCooldownRepository(redis_b, fallback=LocalCooldown(now=clock_b))
    assert await activate(a) == BASE
    assert BASE - 1 <= await activate(b) <= BASE

    redis_b.down = True

    assert await b.is_active() is True
    clock_b.now += BASE
    assert await b.is_active() is False
    assert await activate(b) == 360


async def test_with_redis_up_redis_still_decides(redis: fakeredis.FakeAsyncRedis) -> None:
    # Clearing the key (expiry, or an operator) ends it everywhere, this process included.
    repo = WafCooldownRepository(redis, fallback=LocalCooldown())
    await activate(repo)

    await redis.delete(WAF_COOLDOWN_KEY)

    assert await repo.is_active() is False


# --- Spec 017: one start per block (F1..F3, plan-D1) ---------------------------


class HeldRedis:
    """A fakeredis view whose `held` command waits until `release` is set."""

    def __init__(self, server: fakeredis.FakeServer, held: str) -> None:
        self.fake = fakeredis.FakeAsyncRedis(server=server)
        self.held = held
        self.release = asyncio.Event()

    def __getattr__(self, name: str) -> object:
        command = getattr(self.fake, name)
        if name != self.held:
            return command

        async def held(*args: object, **kwargs: object) -> object:
            await self.release.wait()
            return await command(*args, **kwargs)

        return held


async def start_both(
    server: fakeredis.FakeServer, b_redis: HeldRedis, *, b_base: int = BASE
) -> tuple[int, int]:
    """B starts first and stops at its held command; A starts a cooldown; B goes on."""
    a = WafCooldownRepository(fakeredis.FakeAsyncRedis(server=server), fallback=LocalCooldown())
    b = WafCooldownRepository(b_redis, fallback=LocalCooldown())
    b_task = asyncio.create_task(b.activate(base_seconds=b_base, max_seconds=MAX))
    for _ in range(10):
        await asyncio.sleep(0)
    a_applied = await activate(a)
    b_redis.release.set()
    return a_applied, await b_task


async def test_two_simultaneous_starts_make_one_cooldown() -> None:
    # Spec 017 F1: B saw no cooldown, then read the `last` A had just written
    # and doubled it (A 180, B 360).
    server = fakeredis.FakeServer()

    a_applied, b_applied = await start_both(server, HeldRedis(server, held="get"))

    redis = fakeredis.FakeAsyncRedis(server=server)
    assert a_applied == BASE
    assert b_applied <= BASE
    assert await redis.get(WAF_COOLDOWN_LAST_KEY) == b"180"
    assert await redis.ttl(WAF_COOLDOWN_KEY) <= BASE


async def test_the_loser_of_the_start_never_rewrites_the_winner_cooldown() -> None:
    # B decided to start one, but A wrote first: B joins A's (spec 017 RF-1).
    server = fakeredis.FakeServer()

    a_applied, b_applied = await start_both(server, HeldRedis(server, held="set"), b_base=60)

    assert a_applied == BASE
    assert BASE - 1 <= b_applied <= BASE
    redis = fakeredis.FakeAsyncRedis(server=server)
    assert BASE - 1 <= await redis.ttl(WAF_COOLDOWN_KEY) <= BASE  # not shortened to 60
    assert await redis.get(WAF_COOLDOWN_LAST_KEY) == b"180"


async def test_an_active_cooldown_without_last_is_joined_not_rewritten() -> None:
    # Spec 017 F2: it used to be rewritten with the base, shortening it (600 -> 180).
    clock = FakeMonotonic()
    redis = SwitchableRedis()
    await redis.fake.set(WAF_COOLDOWN_KEY, "1", ex=600)
    repo = WafCooldownRepository(redis, fallback=LocalCooldown(now=clock))

    assert 599 <= await activate(repo) <= 600
    assert 599 <= await redis.fake.ttl(WAF_COOLDOWN_KEY) <= 600

    redis.down = True
    assert await repo.is_active() is True
    clock.now += 600
    assert await activate(repo) == MAX  # grows from 600, capped


async def test_the_last_fraction_of_a_second_still_counts_as_active(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    # Spec 017 F3: measured with PTTL, a live key is never "no cooldown".
    await redis.set(WAF_COOLDOWN_LAST_KEY, "180", ex=MAX)
    await redis.set(WAF_COOLDOWN_KEY, "1", px=400)
    repo = WafCooldownRepository(redis)

    assert await activate(repo) == 1
    assert await redis.get(WAF_COOLDOWN_LAST_KEY) == b"180"  # not grown to 360


async def test_a_cooldown_without_expiry_is_respected(redis: fakeredis.FakeAsyncRedis) -> None:
    # Spec 017 spec-D3: an operator stopping traffic by hand is not overridden.
    await redis.set(WAF_COOLDOWN_KEY, "1")
    repo = WafCooldownRepository(redis)

    assert await activate(repo) == BASE
    assert await redis.ttl(WAF_COOLDOWN_KEY) == -1
    assert await redis.get(WAF_COOLDOWN_LAST_KEY) is None
