import logging

import fakeredis
import pytest

from app.exceptions import OutboundRateLimitedError, UpstreamUnavailableError
from app.services.rate_limiter import RATE_LIMIT_KEY, LocalRateLimiter, OutboundRateLimiter
from tests.services.redis_doubles import DOWN, HUNG, BrokenRedis


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def make_limiter(
    redis: fakeredis.FakeAsyncRedis, clock: FakeClock, *, limit: int = 2, window: int = 60
) -> OutboundRateLimiter:
    return OutboundRateLimiter(redis, limit=limit, window_seconds=window, now=clock)


async def test_requests_within_the_limit_pass_and_the_next_one_is_rejected(
    redis: fakeredis.FakeAsyncRedis, clock: FakeClock
) -> None:
    limiter = make_limiter(redis, clock)
    await limiter.acquire()
    await limiter.acquire()

    with pytest.raises(OutboundRateLimitedError):
        await limiter.acquire()


def test_rejection_is_an_upstream_unavailable_error() -> None:
    # Keeps the standard 502 and its body (spec 008 RNF-1).
    assert issubclass(OutboundRateLimitedError, UpstreamUnavailableError)


async def test_a_rejected_attempt_does_not_consume_quota(
    redis: fakeredis.FakeAsyncRedis, clock: FakeClock
) -> None:
    limiter = make_limiter(redis, clock)
    await limiter.acquire()
    await limiter.acquire()

    for _ in range(3):
        with pytest.raises(OutboundRateLimitedError):
            await limiter.acquire()

    assert await redis.zcard(RATE_LIMIT_KEY) == 2


async def test_quota_comes_back_once_the_window_has_passed(
    redis: fakeredis.FakeAsyncRedis, clock: FakeClock
) -> None:
    limiter = make_limiter(redis, clock)
    await limiter.acquire()
    await limiter.acquire()

    clock.now += 61

    await limiter.acquire()


async def test_the_window_slides_instead_of_resetting(
    redis: fakeredis.FakeAsyncRedis, clock: FakeClock
) -> None:
    # A fixed window would allow 2x the limit around its boundary (plan-D3).
    limiter = make_limiter(redis, clock)
    start = clock.now
    await limiter.acquire()  # t=0
    clock.now = start + 59
    await limiter.acquire()  # t=59

    clock.now = start + 61
    await limiter.acquire()  # t=0 has left the window

    clock.now = start + 62
    with pytest.raises(OutboundRateLimitedError):
        await limiter.acquire()  # t=59 and t=61 are still inside


async def test_zero_limit_disables_it_without_touching_redis(
    redis: fakeredis.FakeAsyncRedis, clock: FakeClock
) -> None:
    limiter = make_limiter(redis, clock, limit=0)

    for _ in range(50):
        await limiter.acquire()

    assert await redis.exists(RATE_LIMIT_KEY) == 0


async def test_the_key_expires_with_the_window(
    redis: fakeredis.FakeAsyncRedis, clock: FakeClock
) -> None:
    await make_limiter(redis, clock).acquire()

    assert RATE_LIMIT_KEY == "ratelimit:alcampo"
    assert 59 <= await redis.ttl(RATE_LIMIT_KEY) <= 60


# --- spec 007: configurable key and local fallback without Redis ---------------


async def test_limiters_with_different_keys_do_not_share_quota(
    redis: fakeredis.FakeAsyncRedis, clock: FakeClock
) -> None:
    searches = OutboundRateLimiter(redis, limit=1, window_seconds=60, now=clock)
    resolutions = OutboundRateLimiter(
        redis, limit=1, window_seconds=60, now=clock, key="ratelimit:alcampo:region-resolutions"
    )
    await searches.acquire()

    await resolutions.acquire()  # its own quota

    with pytest.raises(OutboundRateLimitedError):
        await searches.acquire()


@pytest.mark.parametrize("error", [DOWN, HUNG], ids=["down", "hung"])
async def test_without_redis_the_local_fallback_keeps_limiting(
    clock: FakeClock, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    limiter = OutboundRateLimiter(
        BrokenRedis(error), limit=2, window_seconds=60, now=clock, fallback=LocalRateLimiter()
    )
    await limiter.acquire()
    await limiter.acquire()

    with pytest.raises(OutboundRateLimitedError):
        await limiter.acquire()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings
    assert all("redis unavailable" in r.getMessage() for r in warnings)


async def test_the_local_fallback_slides_and_rejections_do_not_consume_quota(
    clock: FakeClock,
) -> None:
    limiter = OutboundRateLimiter(
        BrokenRedis(DOWN), limit=2, window_seconds=60, now=clock, fallback=LocalRateLimiter()
    )
    start = clock.now
    await limiter.acquire()  # t=0
    clock.now = start + 59
    await limiter.acquire()  # t=59
    with pytest.raises(OutboundRateLimitedError):
        await limiter.acquire()  # rejected: must not consume quota

    clock.now = start + 61
    await limiter.acquire()  # t=0 has left the window
    clock.now = start + 62
    with pytest.raises(OutboundRateLimitedError):
        await limiter.acquire()


async def test_limiters_sharing_a_fallback_share_its_quota(clock: FakeClock) -> None:
    # Limiters are built per request: the fallback must live in app.state.
    fallback = LocalRateLimiter()
    first = OutboundRateLimiter(
        BrokenRedis(DOWN), limit=1, window_seconds=60, now=clock, fallback=fallback
    )
    second = OutboundRateLimiter(
        BrokenRedis(DOWN), limit=1, window_seconds=60, now=clock, fallback=fallback
    )
    await first.acquire()

    with pytest.raises(OutboundRateLimitedError):
        await second.acquire()
