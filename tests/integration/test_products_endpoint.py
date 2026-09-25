from fastapi.testclient import TestClient

from tests.integration.conftest import load_fixture, mock_alcampo_search


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
    assert await client.app.state.redis.dbsize() == 0
