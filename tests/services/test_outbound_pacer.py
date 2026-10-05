"""Spec 010 RF-3: requests of a process leave spaced, not in a burst."""

import asyncio

import pytest

from app.services.outbound import OutboundPacer


class FakeTime:
    """A clock whose `sleep` advances it, recording every wait."""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(round(seconds, 3))
        self.now += seconds
        await asyncio.sleep(0)


@pytest.fixture
def time_() -> FakeTime:
    return FakeTime()


def pacer(
    time_: FakeTime, *, min_ms: int = 500, jitter_ms: int = 500, jitter: float = 0.2
) -> OutboundPacer:
    return OutboundPacer(
        min_interval_ms=min_ms,
        jitter_ms=jitter_ms,
        now=time_.clock,
        sleep=time_.sleep,
        uniform=lambda low, high: low + (high - low) * jitter,
    )


async def test_the_first_request_does_not_wait(time_: FakeTime) -> None:
    assert await pacer(time_).wait_turn() == 0
    assert time_.sleeps == []


async def test_the_next_request_waits_the_minimum_plus_jitter(time_: FakeTime) -> None:
    p = pacer(time_)  # 500 ms + 20 % of 500 ms of jitter = 0.6 s
    await p.wait_turn()

    waited = await p.wait_turn()

    assert waited == pytest.approx(0.6)
    assert time_.sleeps == [0.6]


async def test_time_already_elapsed_counts(time_: FakeTime) -> None:
    p = pacer(time_)
    await p.wait_turn()
    time_.now += 0.4  # the request itself took 0.4 s

    assert await p.wait_turn() == pytest.approx(0.2)


async def test_concurrent_requests_leave_one_after_another(time_: FakeTime) -> None:
    p = pacer(time_, jitter=0.0)  # exactly 0.5 s apart

    await asyncio.gather(*(p.wait_turn() for _ in range(4)))

    assert time_.sleeps == [0.5, 0.5, 0.5]
    assert time_.now == pytest.approx(101.5)


async def test_zero_minimum_disables_the_spacing(time_: FakeTime) -> None:
    p = pacer(time_, min_ms=0)

    for _ in range(3):
        assert await p.wait_turn() == 0

    assert time_.sleeps == []
