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
