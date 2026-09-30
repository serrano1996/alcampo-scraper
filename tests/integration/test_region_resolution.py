"""Spec 007 end to end: real app, `lifespan`, fakeredis, and a stateful fake Alcampo.

`FakeAlcampo` keeps one region per session cookie, as the real one does (Fase 0
§3): `activate` moves a session, the home page shows where it is, and every
search records the region of the session it came with. That lets these tests
check the one thing spec 007 must never get wrong: each postal code searched
in its own region.
"""

import itertools
import json
import logging
from collections import Counter
from collections.abc import Iterator
from urllib.parse import parse_qs

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.state import resources
from app.main import create_app
from tests.integration.conftest import (
    ALCAMPO_BASE_URL,
    SEARCH_URL,
    TEST_API_KEY,
    load_fixture,
)

VAGUADA = "ac90d761-9d58-4918-a37d-dd14e1ce384a"
TELDE = "c98744f2-ca04-4583-bbfc-c52f24548329"
RETAILER = {VAGUADA: "5", TELDE: "32"}
REGION_OF = {"28001": VAGUADA, "35001": TELDE, "35017": TELDE, "51001": VAGUADA}
NOT_DELIVERABLE = {"51001"}
CSRF = "fake-csrf-token-e2e"
VISITOR = "fake-visitor-id-e2e"


class FakeAlcampo:
    def __init__(self, router: respx.MockRouter) -> None:
        self.session_region: dict[str, str] = {}
        self.destination_region: dict[str, str] = {}
        self.search_regions: list[str] = []
        self.calls: Counter[str] = Counter()
        self.challenge_on: str | None = None
        self._ids = itertools.count(1)
        base = ALCAMPO_BASE_URL
        router.get(f"{base}/").mock(side_effect=self.home)
        router.put(f"{base}/api/address/v1/addresses/areas").mock(side_effect=self.areas)
        router.get(url__regex=rf"{base}/api/address/v1/addresses/areas/.+").mock(
            side_effect=self.area_details
        )
        router.put(f"{base}/api/ecomdeliverydestinations/v2/deliverability").mock(
            side_effect=self.deliverability
        )
        router.post(f"{base}/api/ecomdeliverydestinations/v2/temporary-delivery-destinations").mock(
            side_effect=self.create_destination
        )
        router.get(
            url__regex=rf"{base}/api/ecomdeliverydestinations/v4/delivery-addresses/.+"
        ).mock(side_effect=self.delivery_address)
        router.post(f"{base}/api/customersessions/v2/sessions/proposition").mock(
            side_effect=self.propose
        )
        router.post(f"{base}/api/customersessions/v2/sessions/active").mock(
            side_effect=self.activate
        )
        router.get(SEARCH_URL).mock(side_effect=self.search)

    def _step(self, name: str) -> httpx.Response | None:
        self.calls[name] += 1
        if self.challenge_on == name:
            return httpx.Response(202, headers={"x-amzn-waf-action": "challenge"})
        return None

    def _sid(self, request: httpx.Request) -> str | None:
        cookie = request.headers.get("cookie", "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "sid":
                return value
        return None

    def home(self, request: httpx.Request) -> httpx.Response:
        self.calls["home"] += 1
        sid = self._sid(request)
        headers = {}
        if sid is None:
            sid = f"s{next(self._ids)}"
            headers["set-cookie"] = f"sid={sid}; Path=/"
        region = self.session_region.setdefault(sid, VAGUADA)  # anonymous: Vaguada
        state = {
            "session": {"csrf": {"token": CSRF}, "metadata": {"visitorId": VISITOR}},
            "region": {"regionId": region, "retailerRegionId": RETAILER[region]},
        }
        html = f"<html><script>window.__INITIAL_STATE__={json.dumps(state)};</script></html>"
        return httpx.Response(200, html=html, headers=headers)

    def areas(self, request: httpx.Request) -> httpx.Response:
        self.calls["areas"] += 1
        postal_code = parse_qs(request.content.decode())["query"][0]
        known = postal_code in REGION_OF
        return httpx.Response(200, json=[{"id": f"area-{postal_code}"}] if known else [])

    def area_details(self, request: httpx.Request) -> httpx.Response:
        self.calls["area_details"] += 1
        postal_code = request.url.path.rsplit("-", 1)[1]
        return httpx.Response(
            200,
            json={
                "latitude": 40.0,
                "longitude": -3.0,
                "postalCode": postal_code,
                "formattedAddress": f"{postal_code} Somewhere",
            },
        )

    def deliverability(self, request: httpx.Request) -> httpx.Response:
        self.calls["deliverability"] += 1
        postal_code = json.loads(request.content)["postalCode"]
        value = "NOT_DELIVERABLE" if postal_code in NOT_DELIVERABLE else "DELIVERABLE"
        return httpx.Response(200, json={"deliverability": value})

    def create_destination(self, request: httpx.Request) -> httpx.Response:
        if (blocked := self._step("create_destination")) is not None:
            return blocked
        postal_code = json.loads(request.content)["postalCode"]
        destination = f"dest-{postal_code}-{next(self._ids)}"
        self.destination_region[destination] = REGION_OF[postal_code]
        return httpx.Response(200, json=destination)

    def delivery_address(self, request: httpx.Request) -> httpx.Response:
        self.calls["delivery_address"] += 1
        destination = request.url.path.rsplit("/", 1)[1]
        return httpx.Response(200, json={"resolvedRegionId": self.destination_region[destination]})

    def propose(self, request: httpx.Request) -> httpx.Response:
        self.calls["propose"] += 1
        region = json.loads(request.content)["destinationRegionId"]
        return httpx.Response(
            200,
            json={
                "originCartProposition": {"cartPropositionId": "origin"},
                "destinationCartProposition": {"cartPropositionId": f"cart-{region}"},
            },
        )

    def activate(self, request: httpx.Request) -> httpx.Response:
        self.calls["activate"] += 1
        region = json.loads(request.content)["destinationCartPropositionId"].removeprefix("cart-")
        self.session_region[self._sid(request)] = region
        return httpx.Response(200, json={"regionId": region})

    def search(self, request: httpx.Request) -> httpx.Response:
        self.calls["search"] += 1
        self.search_regions.append(RETAILER[self.session_region[self._sid(request)]])
        return httpx.Response(200, json=load_fixture("alcampo_search_leche.json"))


@pytest.fixture
def alcampo(respx_mock: respx.MockRouter) -> FakeAlcampo:
    return FakeAlcampo(respx_mock)


@pytest.fixture
def app_client(integration_env: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # The global outbound limit is covered by spec 008; these tests count chain calls.
    integration_env.setenv("ALCAMPO_RATE_LIMIT", "0")
    with TestClient(create_app(), headers={"X-API-Key": TEST_API_KEY}) as client:
        yield client


def search(client: TestClient, postal_code: str, term: str = "agua") -> httpx.Response:
    return client.get("/api/v1/products", params={"postal_code": postal_code, "term": term})


async def test_each_postal_code_is_searched_in_its_own_region(
    app_client: TestClient, alcampo: FakeAlcampo
) -> None:
    madrid = search(app_client, "28001")
    canarias = search(app_client, "35001")

    assert (madrid.status_code, canarias.status_code) == (200, 200)
    assert madrid.json()["search"]["warehouse"] == "5"
    assert canarias.json()["search"]["warehouse"] == "32"
    assert alcampo.search_regions == ["5", "32"]  # the session really was in each region
    redis = resources(app_client.app).redis
    assert sorted(await redis.keys("search:*")) == [b"search:32:agua", b"search:5:agua"]


def test_a_known_postal_code_costs_no_chain_and_its_region_is_shared(
    app_client: TestClient, alcampo: FakeAlcampo
) -> None:
    search(app_client, "35001")
    first = alcampo.calls.copy()

    search(app_client, "35001", term="leche")  # known postal code, same session
    assert alcampo.calls["areas"] == first["areas"]
    assert alcampo.calls["create_destination"] == first["create_destination"]

    search(app_client, "35017", term="pan")  # new postal code, same region
    assert alcampo.calls["create_destination"] == first["create_destination"] + 1
    assert alcampo.calls["propose"] == first["propose"]  # no new confirmation
    assert alcampo.search_regions == ["32", "32", "32"]


@pytest.mark.parametrize("postal_code", ["99999", "51001"], ids=["unknown", "not-deliverable"])
def test_a_postal_code_not_served_is_a_404_and_is_remembered(
    app_client: TestClient, alcampo: FakeAlcampo, postal_code: str
) -> None:
    first = search(app_client, postal_code)
    calls = alcampo.calls.copy()
    second = search(app_client, postal_code)

    assert (first.status_code, second.status_code) == (404, 404)
    assert first.json() == {"detail": "Postal code not served by Alcampo"}
    assert alcampo.calls == calls  # the second one never left the service
    assert alcampo.calls["create_destination"] == 0


def test_an_exhausted_resolution_limit_spares_known_postal_codes(
    integration_env: pytest.MonkeyPatch, alcampo: FakeAlcampo, caplog: pytest.LogCaptureFixture
) -> None:
    integration_env.setenv("ALCAMPO_RATE_LIMIT", "0")
    integration_env.setenv("REGION_RESOLUTION_LIMIT", "1")
    with TestClient(create_app(), headers={"X-API-Key": TEST_API_KEY}) as client:
        madrid = search(client, "28001")
        canarias = search(client, "35001")
        madrid_again = search(client, "28001", term="leche")

    assert (madrid.status_code, canarias.status_code, madrid_again.status_code) == (200, 502, 200)
    assert alcampo.calls["create_destination"] == 1
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("region resolution limit reached" in message for message in warnings)


async def test_a_waf_challenge_while_resolving_starts_the_cooldown(
    app_client: TestClient, alcampo: FakeAlcampo
) -> None:
    alcampo.challenge_on = "create_destination"

    response = search(app_client, "35001")

    assert response.status_code == 502
    redis = resources(app_client.app).redis
    assert await redis.exists("waf:cooldown") == 1
    assert await redis.exists("postal-code-region:35001") == 0
    assert alcampo.calls["search"] == 0


def test_session_tokens_never_reach_the_logs(
    integration_env: pytest.MonkeyPatch, alcampo: FakeAlcampo, caplog: pytest.LogCaptureFixture
) -> None:
    integration_env.setenv("ALCAMPO_RATE_LIMIT", "0")
    integration_env.setenv("LOG_LEVEL", "DEBUG")
    caplog.set_level(logging.DEBUG)
    with TestClient(create_app(), headers={"X-API-Key": TEST_API_KEY}) as client:
        search(client, "28001")
        search(client, "35001")
        search(client, "51001")

    assert alcampo.calls["search"] == 2  # the chain really ran
    assert CSRF not in caplog.text
    assert VISITOR not in caplog.text
    assert "sid=" not in caplog.text
