"""One confirmed Alcampo session per region, in process memory (spec 007 RF-9..RF-11).

Alcampo takes the region from the session, not from the search request, so each
region needs its own session (its own cookies) moved to it with steps 6-7.

Never search with an unconfirmed session (RF-10): after `activate`, the home
page must show the expected region, or the session is dropped and the search
fails with 502. Serving Madrid prices labelled as Las Palmas is the one thing
this spec must never do.

Sessions are renewed past `max_age_seconds` (50 min, below VISITORID's hour)
by reusing the region's stored delivery destination: 4 requests and no new
destination, so renewals do not touch the strict resolution limit (RF-11 as
amended after the live check, plan §2). A destination Alcampo rejects (4xx)
makes the region forgotten, so the next postal code of that region resolves it
again (plan-D6).

Cookies stay in this process: they are session tokens (RNF-3, spec-D5).
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from app.exceptions import UpstreamUnavailableError
from app.services.in_flight import InFlightSearches
from app.services.region_repository import Region, RegionRepository

logger = logging.getLogger(__name__)


class HomeView(Protocol):
    @property
    def region_id(self) -> str: ...
    @property
    def retailer_region_id(self) -> str: ...


class RegionSession(Protocol):
    """The part of `AlcampoSessionClient` used to confirm and keep a region."""

    async def open(self) -> HomeView: ...
    async def propose(self, region_id: str, destination_id: str) -> tuple[str, str]: ...
    async def activate(self, origin_id: str, destination_id: str) -> str: ...
    async def aclose(self) -> None: ...


@dataclass
class _Entry:
    session: RegionSession
    confirmed_at: float
    # Replaced sessions a search may still be using; closed one renewal later.
    retired: list[RegionSession] = field(default_factory=list)


class RegionSessions:
    """Registry of confirmed sessions, one per region; one instance per process (`lifespan`)."""

    def __init__(
        self,
        *,
        new_session: Callable[[], RegionSession],
        repository: RegionRepository,
        max_age_seconds: int,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._new_session = new_session
        self._repository = repository
        self._max_age = max_age_seconds
        self._now = now
        self._entries: dict[str, _Entry] = {}
        self._renewals: InFlightSearches[RegionSession] = InFlightSearches()

    async def adopt(self, region_id: str, destination_id: str, session: RegionSession) -> str:
        """Confirm `region_id` in an already opened `session`, keep it, return the retailer id.

        Used right after resolving a new region, whose session is already open
        (saves a `GET /`). On failure the session is closed.
        """
        retailer_region_id = await self._confirm(session, region_id, destination_id)
        await self._keep(region_id, session)
        logger.info(
            "region session confirmed region=%s retailer=%s reason=new",
            region_id,
            retailer_region_id,
        )
        return retailer_region_id

    async def get(self, region: Region) -> RegionSession:
        """A confirmed session for `region`, renewed first if missing or too old."""
        entry = self._entries.get(region.region_id)
        if entry is not None and self._now() - entry.confirmed_at < self._max_age:
            return entry.session
        session, _ = await self._renewals.run(region.region_id, lambda: self._renew(region))
        return session

    async def aclose(self) -> None:
        for entry in self._entries.values():
            for session in [entry.session, *entry.retired]:
                await session.aclose()
        self._entries.clear()

    async def _renew(self, region: Region) -> RegionSession:
        session = self._new_session()
        try:
            await session.open()  # a fresh session: CSRF token and visitorId
            await self._confirm(
                session, region.region_id, region.delivery_destination_id, close_on_error=False
            )
        except UpstreamUnavailableError as exc:
            await session.aclose()
            if exc.status_code is not None:
                # Alcampo rejected the stored destination: resolve the region again.
                await self._repository.forget_region(region.region_id)
            raise
        except BaseException:
            await session.aclose()
            raise
        await self._keep(region.region_id, session)
        logger.info(
            "region session confirmed region=%s retailer=%s reason=renewal",
            region.region_id,
            region.retailer_region_id,
        )
        return session

    async def _confirm(
        self,
        session: RegionSession,
        region_id: str,
        destination_id: str,
        *,
        close_on_error: bool = True,
    ) -> str:
        """Steps 6-7, then check the home page really shows `region_id` (RF-10)."""
        try:
            origin_id, destination_cart_id = await session.propose(region_id, destination_id)
            activated = await session.activate(origin_id, destination_cart_id)
            home = await session.open()
            if activated != region_id or home.region_id != region_id:
                logger.error(
                    "region session not confirmed expected=%s activated=%s shown=%s",
                    region_id,
                    activated,
                    home.region_id,
                )
                raise UpstreamUnavailableError("region not confirmed")
        except BaseException:
            if close_on_error:
                await session.aclose()
            raise
        return home.retailer_region_id

    async def _keep(self, region_id: str, session: RegionSession) -> None:
        previous = self._entries.get(region_id)
        retired: list[RegionSession] = []
        if previous is not None:
            # Sessions retired one renewal ago can no longer be in use: close them now.
            for old in previous.retired:
                await old.aclose()
            retired = [previous.session]
        self._entries[region_id] = _Entry(
            session=session, confirmed_at=self._now(), retired=retired
        )
