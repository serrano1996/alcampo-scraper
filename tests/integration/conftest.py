"""Integration harness: real app + real `lifespan`, fakeredis and respx (plan-D11).

`create_redis` is patched to return a brand-new `FakeAsyncRedis()` per test, so
no state leaks between tests even though the app's `lifespan` calls it as if
it were talking to a real Redis instance.
"""

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import fakeredis
import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.config import get_settings
from app.main import create_app
from app.services.region_repository import Region

ALCAMPO_BASE_URL = "https://alcampo.test"
SEARCH_URL = f"{ALCAMPO_BASE_URL}/api/webproductpagews/v6/product-pages/search"
# Synthetic key (constitution #12): configured in the env and sent by `client`.
TEST_API_KEY = "test-key"
FIXTURES = Path(__file__).parents[1] / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def integration_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    """Test environment for the real app. Tests may tweak it before starting a client.

    Clears the cached `Settings` (so env changes are seen) and restores the root
    logger afterwards, since the `lifespan` configures logging (spec 003).
    """
    monkeypatch.setenv("ALCAMPO_BASE_URL", ALCAMPO_BASE_URL)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("RETRY_BASE_DELAY", "0")
    monkeypatch.setenv("RETRY_JITTER_MAX_S", "0")
    monkeypatch.setenv("API_KEYS", TEST_API_KEY)
    monkeypatch.setattr(main_module, "create_redis", lambda _url, **_: fakeredis.FakeAsyncRedis())
    get_settings.cache_clear()

    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    factory = logging.getLogRecordFactory()
    yield monkeypatch
    root.handlers[:] = handlers
    root.setLevel(level)
    logging.setLogRecordFactory(factory)
    get_settings.cache_clear()


@pytest.fixture
def client(integration_env: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Authenticated client: sends the test X-API-Key on every request (spec 004)."""
    with TestClient(create_app(), headers={"X-API-Key": TEST_API_KEY}) as test_client:
        yield test_client


def mock_alcampo_search(
    respx_mock: respx.MockRouter,
    *,
    json_body: dict | None = None,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> respx.Route:
    """Register the search route (and the region chain it needs), returning the search route.

    Since spec 007 every uncached postal code is resolved first, so the chain is
    mocked too; tests keep counting calls on the search route only.
    """
    mock_region_chain(respx_mock)
    return respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(status_code, json=json_body, headers=headers or {})
    )


# The synthetic region every postal code resolves to in the integration tests.
# retailerRegionId "5", as the fixed region of specs 001-008, so their
# `search:5:...` cache keys are unchanged (spec 007 plan §6).
REGION_ID = "ac90d761-9d58-4918-a37d-dd14e1ce384a"
DESTINATION_ID = "5bcf6596-e0d8-46c7-ad60-181ae15b2645"


def mock_region_chain(respx_mock: respx.MockRouter) -> dict[str, respx.Route]:
    """Mock steps 0-7 of the session chain (spec 007), all landing in `REGION_ID`.

    The home page always shows that region, so both a resolution and a session
    confirmation succeed. Returns the routes by step, to count calls.
    """
    base = ALCAMPO_BASE_URL
    return {
        "home": respx_mock.get(f"{base}/").mock(
            return_value=httpx.Response(200, html=_text("alcampo_home_vaguada.html"))
        ),
        "areas": respx_mock.put(f"{base}/api/address/v1/addresses/areas").mock(
            return_value=httpx.Response(200, json=load_fixture("alcampo_address_areas_28001.json"))
        ),
        "area_details": respx_mock.get(
            url__regex=rf"{base}/api/address/v1/addresses/areas/.+"
        ).mock(
            return_value=httpx.Response(
                200, json=load_fixture("alcampo_address_area_details_28001.json")
            )
        ),
        "deliverability": respx_mock.put(
            f"{base}/api/ecomdeliverydestinations/v2/deliverability"
        ).mock(
            return_value=httpx.Response(
                200, json=load_fixture("alcampo_deliverability_deliverable.json")
            )
        ),
        "create_destination": respx_mock.post(
            f"{base}/api/ecomdeliverydestinations/v2/temporary-delivery-destinations"
        ).mock(return_value=httpx.Response(200, json=DESTINATION_ID)),
        "delivery_address": respx_mock.get(
            url__regex=rf"{base}/api/ecomdeliverydestinations/v4/delivery-addresses/.+"
        ).mock(
            return_value=httpx.Response(
                200, json={"deliveryDestinationId": DESTINATION_ID, "resolvedRegionId": REGION_ID}
            )
        ),
        "propose": respx_mock.post(f"{base}/api/customersessions/v2/sessions/proposition").mock(
            return_value=httpx.Response(200, json=load_fixture("alcampo_session_proposition.json"))
        ),
        "activate": respx_mock.post(f"{base}/api/customersessions/v2/sessions/active").mock(
            return_value=httpx.Response(200, json={"regionId": REGION_ID})
        ),
    }


async def seed_region(redis: object, postal_code: str = "28001") -> None:
    """Store `postal_code` -> `REGION_ID` as if it had been resolved before.

    For tests that must not spend requests on the chain (e.g. an outbound limit
    of 1). The region's session is still created on the first cache miss.
    """
    region = Region(
        region_id=REGION_ID, retailer_region_id="5", delivery_destination_id=DESTINATION_ID
    )
    await redis.set(f"postal-code-region:{postal_code}", REGION_ID, ex=3600)
    await redis.set(f"region:{REGION_ID}", region.model_dump_json(), ex=3600)


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
