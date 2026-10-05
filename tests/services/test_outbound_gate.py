"""Spec 010 RF-1, RF-3, RF-5, RF-6: the single gate every request to Alcampo goes through."""

import logging

import pytest

from app.exceptions import OutboundRateLimitedError
from app.services.outbound import OutboundGate, TrafficLog


class Steps:
    def __init__(self) -> None:
        self.log: list[str] = []


class FakePacer:
    def __init__(self, steps: Steps) -> None:
        self.steps = steps

    async def wait_turn(self) -> float:
        self.steps.log.append("pace")
        return 0.0


class FakeLimiter:
    def __init__(self, steps: Steps, name: str, *, exhausted: bool = False) -> None:
        self.steps = steps
        self.name = name
        self.exhausted = exhausted
        self.released: list[str] = []

    async def acquire(self) -> str:
        self.steps.log.append(self.name)
        if self.exhausted:
            raise OutboundRateLimitedError("outbound rate limit reached")
        return f"{self.name}-slot"

    async def release(self, slot: str) -> None:
        self.steps.log.append(f"release {slot}")
        self.released.append(slot)


def make_gate(
    *, short_exhausted: bool = False, long_exhausted: bool = False
) -> tuple[OutboundGate, Steps, TrafficLog]:
    steps = Steps()
    traffic = TrafficLog()
    gate = OutboundGate(
        pacer=FakePacer(steps),
        short=FakeLimiter(steps, "short", exhausted=short_exhausted),
        long=FakeLimiter(steps, "long", exhausted=long_exhausted),
        traffic=traffic,
    )
    return gate, steps, traffic


async def test_a_request_is_paced_then_checked_against_both_windows() -> None:
    gate, steps, traffic = make_gate()

    await gate.before_request("search")

    assert steps.log == ["pace", "long", "short"]
    assert traffic.summary().counts["1m"]["search"] == 1


async def test_a_rejection_by_the_short_window_gives_the_long_slot_back() -> None:
    # A rejected request must not consume quota in any window (spec 008 RF-3).
    gate, steps, traffic = make_gate(short_exhausted=True)

    with pytest.raises(OutboundRateLimitedError):
        await gate.before_request("search")

    assert steps.log == ["pace", "long", "short", "release long-slot"]
    assert traffic.summary().counts["1m"]["search"] == 0  # nothing left


async def test_a_rejection_by_the_long_window_never_reaches_the_short_one() -> None:
    gate, steps, _ = make_gate(long_exhausted=True)

    with pytest.raises(OutboundRateLimitedError):
        await gate.before_request("resolution")

    assert steps.log == ["pace", "long"]


@pytest.mark.parametrize("status", [400, 401, 403])
def test_an_unexpected_client_error_is_a_warning_with_its_context(
    caplog: pytest.LogCaptureFixture, status: int
) -> None:
    gate, _, traffic = make_gate()

    gate.after_response(kind="session", endpoint="/api/x", status=status)

    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    message = warning.getMessage()
    assert f"status={status}" in message
    assert "'/api/x'" in message
    assert "kind=session" in message
    assert traffic.summary().client_errors_15m == 1


@pytest.mark.parametrize("status", [200, 202, 404, 429, 503])
def test_expected_answers_are_not_warned(caplog: pytest.LogCaptureFixture, status: int) -> None:
    gate, _, _ = make_gate()

    gate.after_response(kind="search", endpoint="/api/x", status=status)

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
