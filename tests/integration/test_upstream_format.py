"""Spec 011 end to end: Alcampo changing the format of a field the app uses.

The change is simulated on the real `leche` response (tasks rule 4): every
`price.amount` arrives as a number instead of a decimal string.
"""

import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.state import resources
from tests.integration.conftest import SEARCH_URL, load_fixture, mock_region_chain

SEARCH = {"postal_code": "28001", "term": "leche"}


def numeric_prices() -> dict:
    body = load_fixture("alcampo_search_leche.json")
    for group in body["productGroups"]:
        for product in group["decoratedProducts"]:
            product["price"]["amount"] = float(product["price"]["amount"])
    return body


async def test_a_format_change_is_a_502_and_recovers_at_once(
    client: TestClient, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    mock_region_chain(respx_mock)
    route = respx_mock.get(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=numeric_prices()),
            httpx.Response(200, json=load_fixture("alcampo_search_leche.json")),  # fixed
        ]
    )

    broken = client.get("/api/v1/products", params=SEARCH)

    assert broken.status_code == 502
    assert broken.json() == {"detail": "Upstream service unavailable"}
    [error] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error.name == "app.main"
    assert "all products malformed discarded=3" in error.getMessage()
    assert "fields=['price.amount:string_type']" in error.getMessage()
    redis = resources(client.app).redis
    assert await redis.keys("search:*") == []
    assert await redis.exists("waf:cooldown") == 0  # not a WAF block (plan-D4)

    # Alcampo fixed: served at once, not after a cached empty answer expires (H2).
    fixed = client.get("/api/v1/products", params=SEARCH)

    assert fixed.status_code == 200
    assert len(fixed.json()["products"]) == 3
    assert route.call_count == 2
