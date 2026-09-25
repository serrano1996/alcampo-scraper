import httpx
from fastapi.testclient import TestClient

from tests.integration.conftest import SEARCH_URL, load_fixture, mock_alcampo_search


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
    assert await client.app.state.redis.dbsize() == 0


async def test_alcampo_404_returns_502_with_a_single_call(client: TestClient, respx_mock) -> None:
    route = mock_alcampo_search(respx_mock, status_code=404)

    response = client.get("/api/v1/products", params={"postal_code": "28001", "term": "leche"})

    assert response.status_code == 502
    assert route.call_count == 1
    assert await client.app.state.redis.dbsize() == 0


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
