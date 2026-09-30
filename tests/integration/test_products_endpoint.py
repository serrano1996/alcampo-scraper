import asyncio
import logging
import time
from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.product import ProductSearchResponse, SearchMetadata
from tests.integration.conftest import (
    SEARCH_URL,
    TEST_API_KEY,
    load_fixture,
    mock_alcampo_search,
    mock_region_chain,
    seed_region,
)


def test_miss_then_hit(client: TestClient, respx_mock) -> None:
    route = mock_alcampo_search(respx_mock, json_body=load_fixture("alcampo_search_leche.json"))

    first = client.get("/api/v1/products", params={"postal_code": "28001", "term": "leche"})
    second = client.get("/api/v1/products", params={"postal_code": "28001", "term": "leche"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert route.call_count == 1


async def test_search_with_no_results_returns_200_empty_list(
    client: TestClient, respx_mock
) -> None:
    mock_alcampo_search(respx_mock, json_body=load_fixture("alcampo_search_no_results.json"))

    response = client.get("/api/v1/products", params={"postal_code": "28001", "term": "xqzwvkjhgf"})

    assert response.status_code == 200
    assert response.json()["products"] == []


async def test_blank_term_returns_422_without_touching_alcampo_or_redis(
    client: TestClient, respx_mock
) -> None:
    route = mock_alcampo_search(respx_mock, json_body={})

    response = client.get("/api/v1/products", params={"postal_code": "28001", "term": "   "})

    assert response.status_code == 422
    assert route.call_count == 0
    assert await client.app.state.redis.dbsize() == 0


async def test_persistent_5xx_returns_502_after_retry_max_attempts_calls(
    client: TestClient, respx_mock
) -> None:
    route = mock_alcampo_search(respx_mock, status_code=503)

    response = client.get("/api/v1/products", params={"postal_code": "28001", "term": "leche"})

    assert response.status_code == 502
    assert route.call_count == client.app.state.settings.retry_max_attempts
    # Nothing cached. Not dbsize() == 0: the outbound rate limiter now keeps
    # its own key for every request sent (spec 008 T5).
    assert await client.app.state.redis.keys("search:*") == []


async def test_alcampo_404_returns_502_with_a_single_call(client: TestClient, respx_mock) -> None:
    route = mock_alcampo_search(respx_mock, status_code=404)

    response = client.get("/api/v1/products", params={"postal_code": "28001", "term": "leche"})

    assert response.status_code == 502
    assert route.call_count == 1
    # Nothing cached. Not dbsize() == 0: the outbound rate limiter now keeps
    # its own key for every request sent (spec 008 T5).
    assert await client.app.state.redis.keys("search:*") == []


async def test_waf_challenge_returns_502_with_a_single_call(client: TestClient, respx_mock) -> None:
    route = mock_alcampo_search(
        respx_mock, status_code=202, headers={"x-amzn-waf-action": "challenge"}
    )

    response = client.get("/api/v1/products", params={"postal_code": "28001", "term": "leche"})

    assert response.status_code == 502
    assert route.call_count == 1
    redis = client.app.state.redis
    assert await redis.keys("search:*") == []
    assert await redis.exists("waf:cooldown") == 1


async def test_waf_cooldown_end_to_end(client: TestClient, respx_mock) -> None:
    mock_region_chain(respx_mock)
    leche = respx_mock.get(SEARCH_URL, params={"q": "leche"}).mock(
        return_value=httpx.Response(200, json=load_fixture("alcampo_search_leche.json"))
    )
    agua = respx_mock.get(SEARCH_URL, params={"q": "agua"}).mock(
        return_value=httpx.Response(202, headers={"x-amzn-waf-action": "challenge"})
    )

    def search(term: str) -> int:
        params = {"postal_code": "28001", "term": term}
        return client.get("/api/v1/products", params=params).status_code

    assert search("leche") == 200  # cached from now on
    assert search("agua") == 502  # WAF challenge: cooldown starts
    assert agua.call_count == 1

    assert search("agua") == 502  # uncached during cooldown: Alcampo is not called
    assert agua.call_count == 1

    assert search("leche") == 200  # cached during cooldown: still served
    assert leche.call_count == 1


# --- spec 008: outbound rate limit ---------------------------------------------

CACHED_LECHE = ProductSearchResponse(
    search=SearchMetadata(
        postal_code="28001",
        term="leche",
        warehouse="5",
        strategy_used="api",
        scraped_at=datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC),
        total_results=0,
    ),
    products=[],
)


async def test_exhausted_rate_limit_rejects_new_searches_but_serves_the_cache(
    integration_env, respx_mock, caplog
) -> None:
    # Since spec 007 the region chain also takes slots of the global limit, so
    # the setup is seeded instead of spending the single slot on a first search:
    # region and `leche` already known, and the only slot already used.
    integration_env.setenv("ALCAMPO_RATE_LIMIT", "1")
    route = mock_alcampo_search(respx_mock, json_body=load_fixture("alcampo_search_leche.json"))
    with TestClient(create_app(), headers={"X-API-Key": TEST_API_KEY}) as client:
        redis = client.app.state.redis
        await seed_region(redis)
        await redis.set("search:5:leche", CACHED_LECHE.model_dump_json(), ex=3600)
        await redis.zadd("ratelimit:alcampo", {"someone-else": time.time()})

        first = client.get("/api/v1/products", params={"postal_code": "28001", "term": "leche"})
        other = client.get("/api/v1/products", params={"postal_code": "28001", "term": "agua"})
        again = client.get("/api/v1/products", params={"postal_code": "28001", "term": "leche"})

    assert first.status_code == 200
    assert other.status_code == 502
    assert other.json() == {"detail": "Upstream service unavailable"}
    assert again.status_code == 200  # cached: the limit never blocks the cache
    assert route.call_count == 0
    warnings = [r for r in caplog.records if r.name == "app.main" and r.levelno == logging.WARNING]
    assert any("outbound rate limit reached" in r.getMessage() for r in warnings)


# --- spec 008 RF-7: search timeout ---------------------------------------------


def test_slow_alcampo_gets_a_502_within_the_search_timeout(
    integration_env, respx_mock, caplog
) -> None:
    async def never_answers(request: httpx.Request) -> httpx.Response:
        # Valid but late: without a search timeout this would be a 200.
        await asyncio.sleep(5)
        return httpx.Response(200, json=load_fixture("alcampo_search_leche.json"))

    # 1.5 s, not less: since spec 007 the region is resolved first, and a new
    # session builds its own HTTP client (~0.4 s for the TLS context on Windows).
    integration_env.setenv("SEARCH_TIMEOUT_SECONDS", "1.5")
    mock_region_chain(respx_mock)
    respx_mock.get(SEARCH_URL).mock(side_effect=never_answers)
    with TestClient(create_app(), headers={"X-API-Key": TEST_API_KEY}) as client:
        started = time.monotonic()
        response = client.get("/api/v1/products", params={"postal_code": "28001", "term": "leche"})
        elapsed = time.monotonic() - started

    assert response.status_code == 502
    assert response.json() == {"detail": "Upstream service unavailable"}
    assert elapsed < 4  # cut by the timeout, not by Alcampo's 5 s
    errors = [r for r in caplog.records if r.name == "app.main" and r.levelno == logging.ERROR]
    assert any("'search timeout'" in r.getMessage() for r in errors)


# --- spec 007 RF-1: invalid postal code ----------------------------------------


async def test_invalid_postal_code_returns_422_without_touching_alcampo_or_redis(
    client: TestClient, respx_mock
) -> None:
    route = mock_alcampo_search(respx_mock, json_body=load_fixture("alcampo_search_leche.json"))

    response = client.get("/api/v1/products", params={"postal_code": "2800", "term": "leche"})

    assert response.status_code == 422
    assert route.call_count == 0
    assert await client.app.state.redis.dbsize() == 0
