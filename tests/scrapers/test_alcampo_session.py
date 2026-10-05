"""Spec 007 T7: the session chain (Fase 0 §3, verified live in plan §2).

All tokens are synthetic (constitution #12). respx serves every step.
"""

import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import parse_qs

import fakeredis
import httpx
import pytest
import respx

from app.core.config import Settings
from app.exceptions import OutboundRateLimitedError, UpstreamBlockedError, UpstreamUnavailableError
from app.models.alcampo import AlcampoAreaDetails
from app.scrapers.alcampo_session import AlcampoSessionClient
from app.services.rate_limiter import OutboundRateLimiter
from tests.services.outbound_doubles import RecordingGate, gate_for

FIXTURES = Path(__file__).parents[1] / "fixtures"
BASE = "https://alcampo.test"
CSRF = "test-csrf-token-0001"
VISITOR = "test-visitor-id-0001"
VAGUADA = "ac90d761-9d58-4918-a37d-dd14e1ce384a"
TELDE = "c98744f2-ca04-4583-bbfc-c52f24548329"
AREA_ID = "ChIJffesv5coQg0RoKyLM_dAAxw"
DESTINATION = "5bcf6596-e0d8-46c7-ad60-181ae15b2645"

AREAS = f"{BASE}/api/address/v1/addresses/areas"
DELIVERABILITY = f"{BASE}/api/ecomdeliverydestinations/v2/deliverability"
DESTINATIONS = f"{BASE}/api/ecomdeliverydestinations/v2/temporary-delivery-destinations"
PROPOSITION = f"{BASE}/api/customersessions/v2/sessions/proposition"
ACTIVE = f"{BASE}/api/customersessions/v2/sessions/active"


def text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture(name: str) -> object:
    return json.loads(text(name))


def settings() -> Settings:
    return Settings(
        _env_file=None,
        alcampo_base_url=BASE,
        redis_url="redis://localhost:6379/0",
        retry_max_attempts=3,
        retry_base_delay=0,
        retry_jitter_max_s=0,
    )


def limiter(limit: int = 0) -> OutboundRateLimiter:
    return OutboundRateLimiter(fakeredis.FakeAsyncRedis(), limit=limit, window_seconds=60)


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=BASE) as client:
        yield client


def mock_home(router: respx.MockRouter, name: str = "alcampo_home_vaguada.html") -> respx.Route:
    return router.get(f"{BASE}/").mock(return_value=httpx.Response(200, html=text(name)))


async def opened(
    http: httpx.AsyncClient,
    router: respx.MockRouter,
    *,
    rate_limiter: OutboundRateLimiter | None = None,
) -> AlcampoSessionClient:
    mock_home(router)
    session = AlcampoSessionClient(client=http, settings=settings(), gate=gate_for(rate_limiter))
    await session.open()
    return session


def details() -> AlcampoAreaDetails:
    return AlcampoAreaDetails.model_validate(fixture("alcampo_address_area_details_28001.json"))


def assert_session_headers(request: httpx.Request) -> None:
    assert request.headers["X-CSRF-Token"] == CSRF
    assert request.headers["visitorid"] == VISITOR
    assert request.headers["visitor-id"] == VISITOR


# --- step 0: home -------------------------------------------------------------


@respx.mock
async def test_open_reads_the_session_and_region_from_the_home_html(
    http: httpx.AsyncClient,
) -> None:
    session = await opened(http, respx.mock)

    assert session.home.region_id == VAGUADA
    assert session.home.retailer_region_id == "5"
    assert CSRF not in repr(session.home)
    assert VISITOR not in repr(session.home)


@respx.mock
async def test_open_accepts_an_unquoted_retailer_region_id(http: httpx.AsyncClient) -> None:
    mock_home(respx.mock, "alcampo_home_telde.html")
    session = AlcampoSessionClient(client=http, settings=settings(), gate=gate_for())

    home = await session.open()

    assert (home.region_id, home.retailer_region_id) == (TELDE, "32")


@respx.mock
async def test_open_tolerates_whitespace_in_the_embedded_state(http: httpx.AsyncClient) -> None:
    # Today the page embeds compact JSON; a serializer change that adds spaces
    # must not take every region session down (plan R1).
    spaced = (
        '<script>window.__INITIAL_STATE__={"session": {"csrf": {"token": "test-csrf-token-0001"},'
        ' "metadata": {"visitorId": "test-visitor-id-0001"}}, "region": {"regionId": '
        f'"{VAGUADA}", "retailerRegionId": "5"}}}};</script>'
    )
    respx.get(f"{BASE}/").mock(return_value=httpx.Response(200, html=spaced))
    session = AlcampoSessionClient(client=http, settings=settings(), gate=gate_for())

    home = await session.open()

    assert (home.region_id, home.retailer_region_id) == (VAGUADA, "5")
    assert session.home.csrf_token == CSRF


@pytest.mark.parametrize(
    "html",
    [
        text("alcampo_home_vaguada.html").replace('"csrf"', '"nope"'),
        text("alcampo_home_vaguada.html").replace('"visitorId"', '"nope"'),
        text("alcampo_home_vaguada.html").replace('"retailerRegionId"', '"nope"'),
        text("alcampo_home_vaguada.html") + f'<script>{{"regionId":"{TELDE}"}}</script>',
    ],
    ids=["no-csrf", "no-visitor", "no-retailer", "two-regions"],
)
@respx.mock
async def test_unexpected_home_html_is_an_upstream_failure(
    http: httpx.AsyncClient, caplog: pytest.LogCaptureFixture, html: str
) -> None:
    respx.get(f"{BASE}/").mock(return_value=httpx.Response(200, html=html))
    session = AlcampoSessionClient(client=http, settings=settings(), gate=gate_for())

    with pytest.raises(UpstreamUnavailableError) as exc_info:
        await session.open()

    assert "home" in exc_info.value.reason
    assert any(
        r.levelno == logging.ERROR and "step='home'" in r.getMessage() for r in caplog.records
    )


async def test_a_step_before_open_is_a_programming_error(http: httpx.AsyncClient) -> None:
    session = AlcampoSessionClient(client=http, settings=settings(), gate=gate_for())

    with pytest.raises(RuntimeError):
        await session.find_area("28001")


# --- steps 1-5: postal code -> region -----------------------------------------


@respx.mock
async def test_find_area_sends_the_postal_code_as_a_form_with_session_headers(
    http: httpx.AsyncClient,
) -> None:
    session = await opened(http, respx.mock)
    route = respx.put(AREAS).mock(
        return_value=httpx.Response(200, json=fixture("alcampo_address_areas_28001.json"))
    )

    area_id = await session.find_area("28001")

    assert area_id == AREA_ID
    request = route.calls.last.request
    assert parse_qs(request.content.decode()) == {"query": ["28001"]}
    assert_session_headers(request)


@respx.mock
async def test_unknown_postal_code_has_no_area(http: httpx.AsyncClient) -> None:
    session = await opened(http, respx.mock)
    respx.put(AREAS).mock(return_value=httpx.Response(200, json=[]))

    assert await session.find_area("99999") is None


@respx.mock
async def test_area_details_are_parsed(http: httpx.AsyncClient) -> None:
    session = await opened(http, respx.mock)
    respx.get(f"{AREAS}/{AREA_ID}").mock(
        return_value=httpx.Response(200, json=fixture("alcampo_address_area_details_28001.json"))
    )

    area = await session.area_details(AREA_ID)

    assert (area.latitude, area.longitude, area.postal_code) == (40.426175, -3.685144, "28001")
    assert area.formatted_address == "28001 Madrid, España"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("alcampo_deliverability_deliverable.json", "DELIVERABLE"),
        ("alcampo_deliverability_not_deliverable.json", "NOT_DELIVERABLE"),
    ],
)
@respx.mock
async def test_deliverability_is_returned_as_is(
    http: httpx.AsyncClient, name: str, expected: str
) -> None:
    session = await opened(http, respx.mock)
    route = respx.put(DELIVERABILITY).mock(return_value=httpx.Response(200, json=fixture(name)))

    assert await session.deliverability(details()) == expected
    assert json.loads(route.calls.last.request.content) == {
        "latitude": 40.426175,
        "longitude": -3.685144,
        "postalCode": "28001",
    }


@respx.mock
async def test_create_destination_returns_its_id(http: httpx.AsyncClient) -> None:
    session = await opened(http, respx.mock)
    route = respx.post(DESTINATIONS).mock(return_value=httpx.Response(200, json=DESTINATION))

    assert await session.create_destination(details()) == DESTINATION
    assert json.loads(route.calls.last.request.content) == {
        "visitorId": VISITOR,
        "latitude": 40.426175,
        "longitude": -3.685144,
        "postalCode": "28001",
        "formattedAddress": "28001 Madrid, España",
    }
    assert_session_headers(route.calls.last.request)


@respx.mock
async def test_writes_are_never_retried(http: httpx.AsyncClient) -> None:
    # Retrying "create destination" after a 503 could create two of them, and
    # that is the most WAF-sensitive step (Fase 0 §5).
    session = await opened(http, respx.mock)
    route = respx.post(DESTINATIONS).mock(return_value=httpx.Response(503))

    with pytest.raises(UpstreamUnavailableError):
        await session.create_destination(details())

    assert route.call_count == 1


@respx.mock
async def test_delivery_address_gives_the_resolved_region(http: httpx.AsyncClient) -> None:
    session = await opened(http, respx.mock)
    respx.get(f"{BASE}/api/ecomdeliverydestinations/v4/delivery-addresses/{DESTINATION}").mock(
        return_value=httpx.Response(200, json=fixture("alcampo_delivery_address_28001.json"))
    )

    assert await session.delivery_address(DESTINATION) == "4ccafbad-0ed4-4271-a4d0-37ade6710304"


@respx.mock
async def test_unexpected_step_response_is_an_upstream_failure(
    http: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    session = await opened(http, respx.mock)
    respx.get(f"{BASE}/api/ecomdeliverydestinations/v4/delivery-addresses/{DESTINATION}").mock(
        return_value=httpx.Response(200, json={"deliveryDestinationId": DESTINATION})
    )

    with pytest.raises(UpstreamUnavailableError) as exc_info:
        await session.delivery_address(DESTINATION)

    assert "delivery_address" in exc_info.value.reason
    assert any("step='delivery_address'" in r.getMessage() for r in caplog.records)


# --- steps 6-7: confirm the region in the session -----------------------------


@respx.mock
async def test_propose_and_activate_confirm_a_region(http: httpx.AsyncClient) -> None:
    session = await opened(http, respx.mock)
    proposition = respx.post(PROPOSITION).mock(
        return_value=httpx.Response(200, json=fixture("alcampo_session_proposition.json"))
    )
    active = respx.post(ACTIVE).mock(
        return_value=httpx.Response(200, json=fixture("alcampo_session_active.json"))
    )

    origin, destination = await session.propose(TELDE, DESTINATION)
    region = await session.activate(origin, destination)

    assert json.loads(proposition.calls.last.request.content) == {
        "destinationRegionId": TELDE,
        "deliveryDestinationId": DESTINATION,
    }
    assert json.loads(active.calls.last.request.content) == {
        "destinationCartPropositionId": "22222222-2222-4222-8222-222222222222",
        "originCartPropositionId": "11111111-1111-4111-8111-111111111111",
    }
    assert active.calls.last.request.headers["customer-id"] == ""
    assert_session_headers(active.calls.last.request)
    assert region == TELDE


# --- protections (spec 002, 008) and secrets (RF-17) -------------------------


@respx.mock
async def test_every_request_takes_a_slot_of_the_global_limit(http: httpx.AsyncClient) -> None:
    exhausted = limiter(limit=1)
    session = await opened(http, respx.mock, rate_limiter=exhausted)  # home took the slot
    route = respx.put(AREAS).mock(return_value=httpx.Response(200, json=[]))

    with pytest.raises(OutboundRateLimitedError):
        await session.find_area("28001")

    assert route.call_count == 0


@respx.mock
async def test_a_waf_challenge_on_any_step_is_reported_as_blocked(
    http: httpx.AsyncClient,
) -> None:
    session = await opened(http, respx.mock)
    respx.post(DESTINATIONS).mock(
        return_value=httpx.Response(202, headers={"x-amzn-waf-action": "challenge"})
    )

    with pytest.raises(UpstreamBlockedError):
        await session.create_destination(details())


@respx.mock
async def test_session_tokens_never_reach_the_logs(
    http: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    session = await opened(http, respx.mock)
    respx.put(AREAS).mock(return_value=httpx.Response(500))
    respx.post(DESTINATIONS).mock(return_value=httpx.Response(200, json=DESTINATION))

    with pytest.raises(UpstreamUnavailableError):
        await session.find_area("28001")
    await session.create_destination(details())

    assert CSRF not in caplog.text
    assert VISITOR not in caplog.text


# --- spec 010: each step is announced to the gate with its kind -----------------


@respx.mock
async def test_each_step_goes_through_the_gate_with_its_kind(http: httpx.AsyncClient) -> None:
    mock_home(respx.mock)
    respx.put(AREAS).mock(return_value=httpx.Response(200, json=[]))
    respx.post(PROPOSITION).mock(
        return_value=httpx.Response(200, json=fixture("alcampo_session_proposition.json"))
    )
    respx.post(ACTIVE).mock(return_value=httpx.Response(401, json={"code": "CC-090"}))
    gate = RecordingGate()
    session = AlcampoSessionClient(client=http, settings=settings(), gate=gate)

    await session.open()
    await session.find_area("99999")
    origin, destination = await session.propose(TELDE, DESTINATION)
    with pytest.raises(UpstreamUnavailableError):
        await session.activate(origin, destination)

    # Resolution steps (1-5) and session steps (home, 6, 7) are told apart (plan-D5).
    assert gate.kinds == ["session", "resolution", "session", "session"]
    assert [status for _, _, status in gate.answers] == [200, 200, 200, 401]
    assert gate.answers[-1][:2] == ("session", "/api/customersessions/v2/sessions/active")


# --- spec 009 RF-7: page tokens live and die with the session (plan-D1) ---------


def test_a_new_session_knows_no_page_tokens(http: httpx.AsyncClient) -> None:
    session = AlcampoSessionClient(client=http, settings=settings(), gate=gate_for())

    assert session.cursors == {}
    session.cursors[("leche", 50, 2)] = "tok-2"
    other = AlcampoSessionClient(client=http, settings=settings(), gate=gate_for())
    assert other.cursors == {}
