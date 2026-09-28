import fakeredis
import pytest

from app.exceptions import OutboundRateLimitedError, UpstreamUnavailableError
from app.services.rate_limiter import RATE_LIMIT_KEY, OutboundRateLimiter


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
