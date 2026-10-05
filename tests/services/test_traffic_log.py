"""Spec 010 RF-6: what this instance sent to Alcampo lately, for every challenge."""

import pytest

from app.services.outbound import TrafficLog


class FakeClock:
    def __init__(self) -> None:
        self.now = 10_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


def test_an_empty_log_reports_zeros(clock: FakeClock) -> None:
    summary = TrafficLog(now=clock).summary()

    assert summary.counts == {
        "1m": {"search": 0, "resolution": 0, "session": 0},
        "5m": {"search": 0, "resolution": 0, "session": 0},
        "15m": {"search": 0, "resolution": 0, "session": 0},
    }
    assert summary.client_errors_15m == 0


def test_requests_are_counted_by_kind_and_window(clock: FakeClock) -> None:
    log = TrafficLog(now=clock)
    log.record("search")  # t = -600 s
    clock.now += 360
    log.record("resolution")  # t = -240 s
    log.record("session")
    clock.now += 210
    log.record("search")  # t = -30 s
    clock.now += 30

    counts = log.summary().counts

    assert counts["1m"] == {"search": 1, "resolution": 0, "session": 0}
    assert counts["5m"] == {"search": 1, "resolution": 1, "session": 1}
    assert counts["15m"] == {"search": 2, "resolution": 1, "session": 1}


def test_only_client_errors_are_counted_as_4xx(clock: FakeClock) -> None:
    log = TrafficLog(now=clock)
    for status in (200, 400, 401, 404, 429, 500, 202):
        log.record_status(status)

    assert log.summary().client_errors_15m == 4  # 400, 401, 404, 429


def test_entries_older_than_15_minutes_are_dropped(clock: FakeClock) -> None:
    log = TrafficLog(now=clock)
    log.record("search")
    log.record_status(400)

    clock.now += 901

    summary = log.summary()
    assert summary.counts["15m"]["search"] == 0
    assert summary.client_errors_15m == 0
    assert len(log) == 0  # memory does not grow without bound


def test_the_summary_reads_as_one_log_field(clock: FakeClock) -> None:
    log = TrafficLog(now=clock)
    log.record("search")
    log.record("resolution")
    log.record_status(401)

    assert str(log.summary()) == (
        "1m[search=1 resolution=1 session=0] "
        "5m[search=1 resolution=1 session=0] "
        "15m[search=1 resolution=1 session=0] 4xx_15m=1"
    )
