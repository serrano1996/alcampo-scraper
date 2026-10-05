"""Postal code -> region (spec 007 RF-2..RF-8).

1. Memory/Redis first (`RegionRepository`): most searches cost nothing here.
2. Unknown postal codes are resolved once, even with many simultaneous
   searches (the same `InFlightSearches` as spec 008).
3. The chain (Fase 0 §3) stops early and for free when the postal code does not
   exist or is not deliverable (404, cached for an hour).
4. Only then, right before creating a delivery destination (the step the WAF
   punishes), a slot of the strict resolution limit is taken (RF-8, plan-D3).
5. A region seen for the first time keeps the session that resolved it: it is
   handed to `RegionSessions` to be confirmed (steps 6-7), which saves a `GET /`.
   A region already known reuses its record and the session is closed (plan-D4).
"""

import asyncio
import logging
from collections.abc import Callable
from typing import NoReturn, Protocol

from app.core.config import Settings
from app.exceptions import (
    CooldownActiveError,
    OutboundRateLimitedError,
    PostalCodeNotServedError,
    RegionResolutionLimitedError,
    UpstreamUnavailableError,
)
from app.models.alcampo import AlcampoAreaDetails
from app.services.in_flight import InFlightSearches
from app.services.region_repository import NOT_SERVED, Region, RegionRepository
from app.services.region_sessions import RegionSession

DELIVERABLE = "DELIVERABLE"
# Its own window, separate from the global one (spec 007 RF-8, plan-D3).
REGION_RESOLUTIONS_KEY = "ratelimit:alcampo:region-resolutions"

logger = logging.getLogger(__name__)


class ChainSession(RegionSession, Protocol):
    """The part of `AlcampoSessionClient` used to resolve a postal code.

    It extends `RegionSession`: the session that resolves a new region is
    handed to `RegionSessions.adopt`, which confirms it (steps 6-7).
    """

    # open() and aclose() come from RegionSession.
    async def find_area(self, postal_code: str) -> str | None: ...
    async def area_details(self, area_id: str) -> AlcampoAreaDetails: ...
    async def deliverability(self, area: AlcampoAreaDetails) -> str: ...
    async def create_destination(self, area: AlcampoAreaDetails) -> str: ...
    async def delivery_address(self, destination_id: str) -> str: ...


class SessionRegistry(Protocol):
    """The part of `RegionSessions` used here."""

    async def adopt(self, region_id: str, destination_id: str, session: ChainSession) -> str:
        """Confirm `region_id` in `session`, keep it, and return the retailer region id."""
        ...


class ResolutionLimiter(Protocol):
    async def acquire(self) -> str: ...


class CooldownGate(Protocol):
    async def is_active(self) -> bool: ...


class RegionService:
    """Resolve the region that serves a postal code."""

    def __init__(
        self,
        *,
        repository: RegionRepository,
        new_session: Callable[[], ChainSession],
        sessions: SessionRegistry,
        resolution_limiter: ResolutionLimiter,
        cooldown: CooldownGate,
        in_flight: InFlightSearches[Region],
        settings: Settings,
    ) -> None:
        self._repository = repository
        self._new_session = new_session
        self._sessions = sessions
        self._resolution_limiter = resolution_limiter
        self._cooldown = cooldown
        self._in_flight = in_flight
        self._settings = settings

    async def region_for(self, postal_code: str) -> Region:
        """The region of `postal_code`, or `PostalCodeNotServedError`."""
        region_id = await self._repository.region_id_for(postal_code)
        if region_id == NOT_SERVED:
            raise PostalCodeNotServedError(postal_code)
        if region_id is not None:
            region = await self._repository.region(region_id)
            if region is not None:
                self._log(postal_code, region, "cache")
                return region
            # The region was forgotten (its destination stopped working): resolve again.

        region, shared = await self._in_flight.run(
            f"postal-code:{postal_code}", lambda: self._resolve_within_timeout(postal_code)
        )
        self._log(postal_code, region, "shared" if shared else "resolved")
        return region

    async def _resolve_within_timeout(self, postal_code: str) -> Region:
        # Its own budget, like each search (spec 008 RF-7, plan-D9).
        try:
            async with asyncio.timeout(self._settings.search_timeout_seconds):
                return await self._resolve(postal_code)
        except TimeoutError as exc:
            raise UpstreamUnavailableError("region resolution timeout") from exc

    async def _resolve(self, postal_code: str) -> Region:
        # Resolving is traffic to Alcampo too: no chain during a WAF cooldown
        # (spec 007 RF-6). Cached postal codes never get here.
        if await self._cooldown.is_active():
            raise CooldownActiveError("WAF cooldown active")
        session = self._new_session()
        adopted = False
        try:
            await session.open()
            area_id = await session.find_area(postal_code)
            if area_id is None:
                await self._not_served(postal_code)
            area = await session.area_details(area_id)
            if await session.deliverability(area) != DELIVERABLE:
                await self._not_served(postal_code)

            try:
                await self._resolution_limiter.acquire()
            except OutboundRateLimitedError as exc:
                raise RegionResolutionLimitedError("region resolution limit reached") from exc
            destination_id = await session.create_destination(area)
            region_id = await session.delivery_address(destination_id)

            region = await self._repository.region(region_id)
            if region is None:
                adopted = True  # from here on the registry owns (and closes) the session
                retailer_region_id = await self._sessions.adopt(region_id, destination_id, session)
                region = Region(
                    region_id=region_id,
                    retailer_region_id=retailer_region_id,
                    delivery_destination_id=destination_id,
                )
                await self._repository.save_region(region)
            await self._repository.save_postal_code(postal_code, region_id)
            return region
        finally:
            if not adopted:
                await session.aclose()

    async def _not_served(self, postal_code: str) -> NoReturn:
        await self._repository.save_not_served(postal_code)
        raise PostalCodeNotServedError(postal_code)

    @staticmethod
    def _log(postal_code: str, region: Region, source: str) -> None:
        # Never coordinates, addresses or session tokens (spec 007 RF-17).
        logger.info(
            "region resolved postal_code=%r region=%s source=%s",
            postal_code,
            region.region_id,
            source,
        )
