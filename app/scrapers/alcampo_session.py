"""Client for Alcampo's session chain: postal code -> region, and confirming a region.

Alcampo has no "region" parameter: search results depend on the region stored
in the session (cookies). Resolving a postal code and moving a session to its
region is a chain of calls (Fase 0 §3, verified live in spec 007 plan §2):

    0 GET /                          CSRF token, visitorId, current region (HTML)
    1 PUT  areas (form query=<cp>)   area id, or [] if the postal code does not exist
    2 GET  areas/{id}                coordinates and address
    3 PUT  deliverability            DELIVERABLE / NOT_DELIVERABLE
    4 POST temporary destination     destination id  <- the most WAF-sensitive step
    5 GET  delivery-addresses/{id}   resolved region id
    6 POST sessions/proposition      cart proposition ids (preview only)
    7 POST sessions/active           confirms the region in this session

One instance per session, on its own `httpx.AsyncClient` (its own cookies):
sharing the app-wide client would move every search to that region (plan-D1).

The CSRF token and the visitorId are session secrets: they are only sent as
headers, never logged, and hidden from `repr` (spec 007 RF-17, RNF-3).
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Annotated, NoReturn, TypeVar

import httpx
from pydantic import BaseModel, StringConstraints, TypeAdapter, ValidationError

from app.core.config import Settings
from app.exceptions import UpstreamUnavailableError
from app.models.alcampo import (
    AlcampoActiveSession,
    AlcampoArea,
    AlcampoAreaDetails,
    AlcampoDeliverability,
    AlcampoDeliveryAddress,
    AlcampoSessionProposition,
)
from app.scrapers.retry import send_with_retry
from app.services.rate_limiter import OutboundRateLimiter

AREAS_PATH = "/api/address/v1/addresses/areas"
DELIVERABILITY_PATH = "/api/ecomdeliverydestinations/v2/deliverability"
DESTINATIONS_PATH = "/api/ecomdeliverydestinations/v2/temporary-delivery-destinations"
DELIVERY_ADDRESS_PATH = "/api/ecomdeliverydestinations/v4/delivery-addresses/{id}"
PROPOSITION_PATH = "/api/customersessions/v2/sessions/proposition"
ACTIVE_PATH = "/api/customersessions/v2/sessions/active"

# Fragments of the server-side state embedded in the home page. Each must yield
# exactly one distinct value; anything else means the page changed (RF-5).
# Whitespace around `:` and `{` is tolerated: the page embeds compact JSON today,
# and a serializer change must not take every region session down (plan R1).
_HOME_PATTERNS = {
    "csrf_token": re.compile(r'"csrf"\s*:\s*\{\s*"token"\s*:\s*"([^"]+)"'),
    "visitor_id": re.compile(r'"visitorId"\s*:\s*"([^"]+)"'),
    "region_id": re.compile(r'"regionId"\s*:\s*"([0-9a-f-]{36})"'),
    "retailer_region_id": re.compile(r'"retailerRegionId"\s*:\s*"?([0-9A-Za-z-]+)"?'),
}

_DESTINATION_ID = TypeAdapter(Annotated[str, StringConstraints(min_length=1)])

ModelT = TypeVar("ModelT", bound=BaseModel)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HomeState:
    """What the home page says about this session. Secrets are left out of `repr`."""

    region_id: str
    retailer_region_id: str
    csrf_token: str = field(repr=False)
    visitor_id: str = field(repr=False)


class AlcampoSessionClient:
    """One Alcampo session: `open()` first, then the chain steps (spec 007 RF-2, RF-9)."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        settings: Settings,
        rate_limiter: OutboundRateLimiter,
    ) -> None:
        self._client = client
        self._settings = settings
        self._rate_limiter = rate_limiter
        self._home: HomeState | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        """The HTTP client holding this session's cookies (used to search its region)."""
        return self._client

    @property
    def home(self) -> HomeState:
        if self._home is None:
            raise RuntimeError("AlcampoSessionClient.open() has not been called")
        return self._home

    async def open(self) -> HomeState:
        """Step 0 (and the check after step 7): read the session state from the home page."""
        response = await self._send("GET", "/", headers={"Accept": "text/html"})
        values: dict[str, str] = {}
        for name, pattern in _HOME_PATTERNS.items():
            found = set(pattern.findall(response.text))
            if len(found) != 1:
                # Never the values themselves: they include session secrets.
                self._unexpected("home", "/", detail=f"{name} matches={len(found)}")
            values[name] = found.pop()
        self._home = HomeState(**values)
        return self._home

    async def find_area(self, postal_code: str) -> str | None:
        """Step 1: the area id of a postal code, or `None` if Alcampo does not know it."""
        response = await self._send(
            "PUT", AREAS_PATH, data={"query": postal_code}, headers=self._headers()
        )
        areas = self._parse("areas", AREAS_PATH, response, TypeAdapter(list[AlcampoArea]))
        return areas[0].id if areas else None

    async def area_details(self, area_id: str) -> AlcampoAreaDetails:
        """Step 2."""
        path = f"{AREAS_PATH}/{area_id}"
        response = await self._send("GET", path, headers=self._headers())
        return self._parse("area_details", path, response, AlcampoAreaDetails)

    async def deliverability(self, area: AlcampoAreaDetails) -> str:
        """Step 3: returned as is; the caller decides what "not served" means."""
        response = await self._send(
            "PUT",
            DELIVERABILITY_PATH,
            payload={
                "latitude": area.latitude,
                "longitude": area.longitude,
                "postalCode": area.postal_code,
            },
            headers=self._headers(),
        )
        parsed = self._parse("deliverability", DELIVERABILITY_PATH, response, AlcampoDeliverability)
        return parsed.deliverability

    async def create_destination(self, area: AlcampoAreaDetails) -> str:
        """Step 4: the WAF-sensitive one. Callers take a region-resolution slot first."""
        response = await self._send(
            "POST",
            DESTINATIONS_PATH,
            payload={
                "visitorId": self.home.visitor_id,
                "latitude": area.latitude,
                "longitude": area.longitude,
                "postalCode": area.postal_code,
                "formattedAddress": area.formatted_address,
            },
            headers=self._headers(),
        )
        return self._parse("create_destination", DESTINATIONS_PATH, response, _DESTINATION_ID)

    async def delivery_address(self, destination_id: str) -> str:
        """Step 5: the region id that serves `destination_id`."""
        path = DELIVERY_ADDRESS_PATH.format(id=destination_id)
        response = await self._send("GET", path, headers=self._headers())
        return self._parse(
            "delivery_address", path, response, AlcampoDeliveryAddress
        ).resolved_region_id

    async def propose(self, region_id: str, destination_id: str) -> tuple[str, str]:
        """Step 6: `(origin, destination)` cart proposition ids for `activate`."""
        response = await self._send(
            "POST",
            PROPOSITION_PATH,
            payload={"destinationRegionId": region_id, "deliveryDestinationId": destination_id},
            headers=self._headers(),
        )
        proposition = self._parse("propose", PROPOSITION_PATH, response, AlcampoSessionProposition)
        return proposition.origin.cart_proposition_id, proposition.destination.cart_proposition_id

    async def activate(self, origin_id: str, destination_id: str) -> str:
        """Step 7: confirm the region change; returns the region the session is now in."""
        response = await self._send(
            "POST",
            ACTIVE_PATH,
            payload={
                "destinationCartPropositionId": destination_id,
                "originCartPropositionId": origin_id,
            },
            headers={**self._headers(), "customer-id": ""},
        )
        return self._parse("activate", ACTIVE_PATH, response, AlcampoActiveSession).region_id

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        home = self.home
        return {
            "X-CSRF-Token": home.csrf_token,
            "visitorid": home.visitor_id,
            "visitor-id": home.visitor_id,
        }

    async def _send(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        data: dict[str, str] | None = None,
        payload: dict[str, str | float] | None = None,
    ) -> httpx.Response:
        """Every step goes through the global limit, the WAF check and retries (RF-6, plan-D2).

        Only reads are retried: repeating a write after a 5xx could create a
        second destination, on the step the WAF punishes (Fase 0 §5).
        """

        async def send() -> httpx.Response:
            await self._rate_limiter.acquire()
            return await self._client.request(
                method, path, headers=headers, data=data, json=payload
            )

        return await send_with_retry(
            send,
            max_attempts=self._settings.retry_max_attempts if method == "GET" else 1,
            base_delay=self._settings.retry_base_delay,
            jitter_max=self._settings.retry_jitter_max_s,
            url=path,
        )

    def _parse(
        self,
        step: str,
        path: str,
        response: httpx.Response,
        model: type[ModelT] | TypeAdapter[ModelT],
    ) -> ModelT:
        try:
            body = response.json()
            if isinstance(model, TypeAdapter):
                return model.validate_python(body)
            return model.model_validate(body)
        except (json.JSONDecodeError, ValidationError):
            self._unexpected(step, path)

    def _unexpected(self, step: str, path: str, *, detail: str = "") -> NoReturn:
        # The body is Alcampo's and may carry session data: never logged (spec 003 RF-17).
        logger.error("unexpected response from Alcampo step=%r url=%r %s", step, path, detail)
        raise UpstreamUnavailableError(f"unexpected {step} response")
