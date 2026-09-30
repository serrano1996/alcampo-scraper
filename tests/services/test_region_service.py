"""Spec 007 T9: postal code -> region, with fakes for the chain and the sessions."""

import asyncio
import logging

import fakeredis
import pytest

from app.core.config import Settings
from app.exceptions import (
    OutboundRateLimitedError,
    PostalCodeNotServedError,
    RegionResolutionLimitedError,
    UpstreamBlockedError,
    UpstreamThrottledError,
    UpstreamUnavailableError,
)
from app.models.alcampo import AlcampoAreaDetails
from app.services.in_flight import InFlightSearches
from app.services.region_repository import Region, RegionMemory, RegionRepository
from app.services.region_service import RegionService

TELDE_ID = "c98744f2-ca04-4583-bbfc-c52f24548329"
DESTINATION = "44444444-4444-4444-8444-444444444444"
TELDE = Region(region_id=TELDE_ID, retailer_region_id="32", delivery_destination_id=DESTINATION)
AREA = AlcampoAreaDetails(
    latitude=28.1, longitude=-15.4, postal_code="35001", formatted_address="35001 Las Palmas"
)


class FakeSession:
    """Records every step in a shared log; each behaviour can be overridden."""

    def __init__(self, log: list[str], **overrides: object) -> None:
        self.log = log
        self.overrides = overrides
        self.closed = False
        self.gate: asyncio.Event | None = None

    def _step(self, name: str, default: object) -> object:
        self.log.append(name)
        value = self.overrides.get(name, default)
        if isinstance(value, BaseException):
            raise value
        return value

    async def open(self) -> None:
        self._step("open", None)

    async def find_area(self, postal_code: str) -> str | None:
        if self.gate is not None:
            await self.gate.wait()
        return self._step("find_area", "area-id")  # type: ignore[return-value]

    async def area_details(self, area_id: str) -> AlcampoAreaDetails:
        return self._step("area_details", AREA)  # type: ignore[return-value]

    async def deliverability(self, area: AlcampoAreaDetails) -> str:
        return self._step("deliverability", "DELIVERABLE")  # type: ignore[return-value]

    async def create_destination(self, area: AlcampoAreaDetails) -> str:
        return self._step("create_destination", DESTINATION)  # type: ignore[return-value]

    async def delivery_address(self, destination_id: str) -> str:
        return self._step("delivery_address", TELDE_ID)  # type: ignore[return-value]

    async def aclose(self) -> None:
        self.closed = True


class FakeSessions:
    """Stands in for RegionSessions: adopts a session and returns the retailer id."""

    def __init__(self, log: list[str], retailer: str = "32") -> None:
        self.log = log
        self.retailer = retailer
        self.adopted: list[tuple[str, str, FakeSession]] = []

    async def adopt(self, region_id: str, destination_id: str, session: FakeSession) -> str:
        self.log.append("adopt")
        self.adopted.append((region_id, destination_id, session))
        return self.retailer


class FakeLimiter:
    def __init__(self, log: list[str], *, exhausted: bool = False) -> None:
        self.log = log
        self.exhausted = exhausted
        self.acquired = 0

    async def acquire(self) -> None:
        self.log.append("limit")
        if self.exhausted:
            raise OutboundRateLimitedError("outbound rate limit reached")
        self.acquired += 1


class Harness:
    def __init__(self, *, timeout: float = 15, exhausted: bool = False, **overrides: object):
        self.log: list[str] = []
        self.redis = fakeredis.FakeAsyncRedis()
        self.repository = RegionRepository(
            self.redis, memory=RegionMemory(), ttl_seconds=604800, negative_ttl_seconds=3600
        )
        self.sessions_made: list[FakeSession] = []
        self.overrides = overrides
        # Set to hold the chain at step 1 until the test releases it.
        self.gate: asyncio.Event | None = None
        self.sessions = FakeSessions(self.log)
        self.limiter = FakeLimiter(self.log, exhausted=exhausted)
        self.service = RegionService(
            repository=self.repository,
            new_session=self.new_session,
            sessions=self.sessions,
            resolution_limiter=self.limiter,
            in_flight=InFlightSearches(),
            settings=Settings(
                _env_file=None,
                alcampo_base_url="https://alcampo.test",
                redis_url="redis://localhost:6379/0",
                search_timeout_seconds=timeout,
            ),
        )

    def new_session(self) -> FakeSession:
        session = FakeSession(self.log, **self.overrides)
        session.gate = self.gate
        self.sessions_made.append(session)
        return session


def records(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "app.services.region_service" and r.levelno == level
    ]


async def test_cached_postal_code_needs_no_request(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    h = Harness()
    await h.repository.save_region(TELDE)
    await h.repository.save_postal_code("35001", TELDE_ID)

    assert await h.service.region_for("35001") == TELDE
    assert h.log == []
    assert records(caplog, logging.INFO) == [
        f"region resolved postal_code='35001' region={TELDE_ID} source=cache"
    ]


async def test_new_postal_code_in_a_new_region_runs_the_chain_and_adopts_the_session(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    h = Harness()

    region = await h.service.region_for("35001")

    assert region == TELDE
    # The resolution slot is taken right before creating the destination, once.
    assert h.log == [
        "open",
        "find_area",
        "area_details",
        "deliverability",
        "limit",
        "create_destination",
        "delivery_address",
        "adopt",
    ]
    [(region_id, destination, session)] = h.sessions.adopted
    assert (region_id, destination) == (TELDE_ID, DESTINATION)
    assert session.closed is False  # now owned by the sessions registry
    assert await h.repository.region(TELDE_ID) == TELDE
    assert await h.repository.region_id_for("35001") == TELDE_ID
    assert records(caplog, logging.INFO)[-1].endswith("source=resolved")


async def test_new_postal_code_in_a_known_region_reuses_its_record() -> None:
    h = Harness()
    await h.repository.save_region(TELDE)

    assert await h.service.region_for("35017") == TELDE
    assert "adopt" not in h.log
    assert h.sessions_made[0].closed is True
    assert await h.repository.region_id_for("35017") == TELDE_ID


@pytest.mark.parametrize(
    "overrides",
    [{"find_area": None}, {"deliverability": "NOT_DELIVERABLE"}],
    ids=["unknown", "not-deliverable"],
)
async def test_not_served_postal_code_is_cached_and_costs_no_resolution_slot(
    overrides: dict[str, object],
) -> None:
    h = Harness(**overrides)

    with pytest.raises(PostalCodeNotServedError):
        await h.service.region_for("51001")
    with pytest.raises(PostalCodeNotServedError):
        await h.service.region_for("51001")  # from the negative cache

    assert "limit" not in h.log
    assert "create_destination" not in h.log
    assert h.log.count("open") == 1
    assert all(session.closed for session in h.sessions_made)


async def test_exhausted_resolution_limit_stops_before_creating_a_destination() -> None:
    h = Harness(exhausted=True)
    await h.repository.save_region(TELDE)
    await h.repository.save_postal_code("35001", TELDE_ID)

    with pytest.raises(RegionResolutionLimitedError) as exc_info:
        await h.service.region_for("08001")

    assert isinstance(exc_info.value, UpstreamThrottledError)  # WARNING + 502
    assert "create_destination" not in h.log
    assert await h.repository.region_id_for("08001") is None
    assert await h.service.region_for("35001") == TELDE  # resolved ones keep working


async def test_simultaneous_resolutions_of_one_postal_code_share_a_chain(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    h = Harness()
    h.gate = asyncio.Event()  # hold the chain until every caller is waiting
    tasks = [asyncio.create_task(h.service.region_for("35001")) for _ in range(10)]
    for _ in range(20):
        await asyncio.sleep(0)
    h.gate.set()

    assert await asyncio.gather(*tasks) == [TELDE] * 10
    assert h.log.count("open") == 1
    sources = [m.rsplit("=", 1)[1] for m in records(caplog, logging.INFO)]
    assert sorted(sources) == ["resolved"] + ["shared"] * 9


async def test_a_waf_challenge_propagates_and_nothing_is_cached() -> None:
    h = Harness(create_destination=UpstreamBlockedError("WAF challenge"))

    with pytest.raises(UpstreamBlockedError):
        await h.service.region_for("35001")

    assert await h.repository.region_id_for("35001") is None
    assert await h.repository.region(TELDE_ID) is None
    assert h.sessions_made[0].closed is True


async def test_a_resolution_past_the_timeout_fails_and_closes_its_session() -> None:
    h = Harness(timeout=0.05)
    h.gate = asyncio.Event()  # never set: Alcampo never answers

    async with asyncio.timeout(2):  # safety net: a missing timeout must fail, not hang
        with pytest.raises(UpstreamUnavailableError) as exc_info:
            await h.service.region_for("35001")

    assert exc_info.value.reason == "region resolution timeout"
    assert h.sessions_made[0].closed is True


async def test_a_forgotten_region_is_resolved_again() -> None:
    h = Harness()
    await h.repository.save_region(TELDE)
    await h.repository.save_postal_code("35001", TELDE_ID)
    await h.repository.forget_region(TELDE_ID)  # its destination stopped working

    assert await h.service.region_for("35001") == TELDE
    assert "create_destination" in h.log
