"""Spec 008 end to end: real app, `lifespan`, fakeredis and respx."""

import logging
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.state import resources
from app.main import create_app
from app.services.rate_limiter import RATE_LIMIT_KEY
from app.services.waf_cooldown import WAF_COOLDOWN_KEY, WAF_COOLDOWN_LAST_KEY
from tests.integration.conftest import TEST_API_KEY, load_fixture, mock_alcampo_search

SEARCH = {"postal_code": "28001", "term": "leche"}


@pytest.fixture
def limited_client(integration_env: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Authenticated client whose outbound limit is a single request per minute."""
    integration_env.setenv("ALCAMPO_RATE_LIMIT", "1")
    with TestClient(create_app(), headers={"X-API-Key": TEST_API_KEY}) as client:
        yield client


def records(caplog: pytest.LogCaptureFixture, name: str, level: int) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == name and r.levelno == level]


async def test_cooldown_is_checked_before_the_rate_limit(
    limited_client: TestClient, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    route = mock_alcampo_search(respx_mock, json_body=load_fixture("alcampo_search_leche.json"))
    redis = resources(limited_client.app).redis
    await redis.set(WAF_COOLDOWN_KEY, "1", ex=180)
    await redis.zadd(RATE_LIMIT_KEY, {"someone-else": time.time()})  # limit exhausted too

    response = limited_client.get("/api/v1/products", params=SEARCH)

    assert response.status_code == 502
    assert route.call_count == 0
    [warning] = records(caplog, "app.main", logging.WARNING)
    assert "'WAF cooldown active'" in warning.getMessage()


def test_a_cache_hit_is_logged_with_its_own_request_id(
    client: TestClient, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    mock_alcampo_search(respx_mock, json_body=load_fixture("alcampo_search_leche.json"))

    miss = client.get("/api/v1/products", params=SEARCH)
    hit = client.get("/api/v1/products", params={**SEARCH, "term": "LECHE"})

    served = {
        r.request_id: r.getMessage()
        for r in records(caplog, "app.services.product_service", logging.INFO)
    }
    assert served[miss.headers["X-Request-ID"]] == "search served source=miss"
    assert served[hit.headers["X-Request-ID"]] == "search served source=hit"


async def test_a_recent_second_challenge_doubles_the_cooldown(
    client: TestClient, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    mock_alcampo_search(respx_mock, status_code=202, headers={"x-amzn-waf-action": "challenge"})
    redis = resources(client.app).redis

    first = client.get("/api/v1/products", params=SEARCH)
    await redis.delete(WAF_COOLDOWN_KEY)  # cooldown over, but the challenge is still recent
    second = client.get("/api/v1/products", params={**SEARCH, "term": "agua"})

    assert (first.status_code, second.status_code) == (502, 502)
    first_error, second_error = records(caplog, "app.services.product_service", logging.ERROR)
    assert "cooldown_s=180" in first_error.getMessage()
    assert "cooldown_s=360" in second_error.getMessage()
    assert await redis.get(WAF_COOLDOWN_LAST_KEY) == b"360"
    assert 359 <= await redis.ttl(WAF_COOLDOWN_KEY) <= 360
