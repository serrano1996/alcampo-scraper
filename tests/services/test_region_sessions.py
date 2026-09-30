"""Spec 007 T10: one confirmed session per region, renewed without new destinations."""

import asyncio
import logging
from dataclasses import dataclass

import fakeredis
import pytest

from app.exceptions import UpstreamUnavailableError
from app.services.region_repository import Region, RegionMemory, RegionRepository
from app.services.region_sessions import RegionSessions

TELDE_ID = "c98744f2-ca04-4583-bbfc-c52f24548329"
VAGUADA_ID = "ac90d761-9d58-4918-a37d-dd14e1ce384a"
DESTINATION = "44444444-4444-4444-8444-444444444444"
TELDE = Region(region_id=TELDE_ID, retailer_region_id="32", delivery_destination_id=DESTINATION)
MAX_AGE = 3000


@dataclass(frozen=True)
class Home:
    region_id: str
    retailer_region_id: str


class FakeSession:
    """Starts in Vaguada (as a fresh anonymous session); `activate` moves it."""

    def __init__(self, *, lands_in: str = TELDE_ID, propose_error: Exception | None = None):
        self.calls: list[str] = []
        self.lands_in = lands_in
        self.propose_error = propose_error
        self.region = VAGUADA_ID
        self.closed = False
        self.gate: asyncio.Event | None = None
        self.client = object()  # stands for the httpx client with this session's cookies

    async def open(self) -> Home:
        self.calls.append("open")
        retailer = "32" if self.region == TELDE_ID else "5"
        return Home(region_id=self.region, retailer_region_id=retailer)

    async def propose(self, region_id: str, destination_id: str) -> tuple[str, str]:
        self.calls.append(f"propose {region_id} {destination_id}")
        if self.gate is not None:
            await self.gate.wait()
        if self.propose_error is not None:
            raise self.propose_error
        return "origin", "destination"

    async def activate(self, origin_id: str, destination_id: str) -> str:
        self.calls.append("activate")
        self.region = self.lands_in
        return self.lands_in

    async def aclose(self) -> None:
        self.closed = True


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Harness:
    def __init__(self, **session_options: object) -> None:
        self.clock = FakeClock()
        self.made: list[FakeSession] = []
        self.session_options = session_options
        self.gate: asyncio.Event | None = None
        self.repository = RegionRepository(
            fakeredis.FakeAsyncRedis(),
            memory=RegionMemory(),
            ttl_seconds=604800,
            negative_ttl_seconds=3600,
        )
        self.sessions = RegionSessions(
            new_session=self.new_session,
            repository=self.repository,
            max_age_seconds=MAX_AGE,
            now=self.clock,
        )

    def new_session(self) -> FakeSession:
        session = FakeSession(**self.session_options)
        session.gate = self.gate
        self.made.append(session)
        return session


def confirmations(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "app.services.region_sessions" and r.levelno == logging.INFO
    ]


async def test_adopt_confirms_the_region_in_the_given_session(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    h = Harness()
    session = FakeSession()

    retailer = await h.sessions.adopt(TELDE_ID, DESTINATION, session)

    assert retailer == "32"
    assert session.calls == [f"propose {TELDE_ID} {DESTINATION}", "activate", "open"]
    assert await h.sessions.get(TELDE) is session  # fresh: no new calls
    assert session.calls == [f"propose {TELDE_ID} {DESTINATION}", "activate", "open"]
    assert confirmations(caplog) == [
        f"region session confirmed region={TELDE_ID} retailer=32 reason=new"
    ]


async def test_a_session_that_lands_in_another_region_is_never_used() -> None:
    h = Harness()
    session = FakeSession(lands_in=VAGUADA_ID)

    with pytest.raises(UpstreamUnavailableError) as exc_info:
        await h.sessions.adopt(TELDE_ID, DESTINATION, session)

    assert exc_info.value.reason == "region not confirmed"
    assert session.closed is True


async def test_a_stale_session_is_renewed_with_the_stored_destination(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    h = Harness()
    first = FakeSession()
    await h.sessions.adopt(TELDE_ID, DESTINATION, first)

    h.clock.now += MAX_AGE + 1
    renewed = await h.sessions.get(TELDE)

    assert renewed is not first
    # open (CSRF) -> propose -> activate -> open (check): no destination is created.
    assert renewed.calls == ["open", f"propose {TELDE_ID} {DESTINATION}", "activate", "open"]
    assert first.closed is False  # a search may still be using it
    assert confirmations(caplog)[-1].endswith("reason=renewal")


async def test_a_region_without_a_session_gets_one_from_its_record() -> None:
    h = Harness()  # e.g. after a restart: the record is in Redis, no session in memory

    session = await h.sessions.get(TELDE)

    assert session.calls == ["open", f"propose {TELDE_ID} {DESTINATION}", "activate", "open"]


async def test_simultaneous_renewals_of_one_region_share_one() -> None:
    h = Harness()
    h.gate = asyncio.Event()
    tasks = [asyncio.create_task(h.sessions.get(TELDE)) for _ in range(5)]
    for _ in range(20):
        await asyncio.sleep(0)
    h.gate.set()

    sessions = await asyncio.gather(*tasks)

    assert len(h.made) == 1
    assert all(session is h.made[0] for session in sessions)


async def test_a_rejected_destination_forgets_the_region() -> None:
    h = Harness(propose_error=UpstreamUnavailableError("gone", status_code=410))
    await h.repository.save_region(TELDE)

    with pytest.raises(UpstreamUnavailableError):
        await h.sessions.get(TELDE)

    assert await h.repository.region(TELDE_ID) is None
    assert h.made[0].closed is True


async def test_a_transient_failure_keeps_the_region() -> None:
    h = Harness(propose_error=UpstreamUnavailableError("upstream returned 503"))
    await h.repository.save_region(TELDE)

    with pytest.raises(UpstreamUnavailableError):
        await h.sessions.get(TELDE)

    assert await h.repository.region(TELDE_ID) == TELDE
    assert h.made[0].closed is True


async def test_retired_sessions_are_closed_at_the_next_renewal_and_on_shutdown() -> None:
    h = Harness()
    first = FakeSession()
    await h.sessions.adopt(TELDE_ID, DESTINATION, first)
    h.clock.now += MAX_AGE + 1
    second = await h.sessions.get(TELDE)

    h.clock.now += MAX_AGE + 1
    third = await h.sessions.get(TELDE)
    assert first.closed is True  # retired one renewal ago: no search can still use it
    assert second.closed is False

    await h.sessions.aclose()
    assert second.closed is True
    assert third.closed is True
