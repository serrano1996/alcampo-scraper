from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.integration.conftest import TEST_API_KEY, load_fixture, mock_alcampo_search

VALID_KEY = TEST_API_KEY
SEARCH = {"postal_code": "28001", "term": "leche"}
UNAUTHORIZED = {"detail": "Invalid or missing API key"}


def start_client(env: pytest.MonkeyPatch, api_keys: str) -> TestClient:
    env.setenv("API_KEYS", api_keys)
    return TestClient(create_app())


@pytest.fixture
def anon_client(integration_env: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """Real app with one configured key and **no** X-API-Key sent by default."""
    with start_client(integration_env, VALID_KEY) as client:
        yield client


@pytest.mark.parametrize(
    "headers",
    [{}, {"X-API-Key": ""}, {"X-API-Key": "wrong"}],
    ids=["missing", "empty", "invalid"],
)
def test_requests_without_a_valid_key_get_the_same_401(
    anon_client: TestClient, headers: dict[str, str]
) -> None:
    response = anon_client.get("/api/v1/products", params=SEARCH, headers=headers)

    assert response.status_code == 401
    assert response.json() == UNAUTHORIZED
    assert response.headers["WWW-Authenticate"] == "ApiKey"


def test_valid_key_reaches_the_endpoint(anon_client: TestClient, respx_mock) -> None:
    mock_alcampo_search(respx_mock, json_body=load_fixture("alcampo_search_leche.json"))

    response = anon_client.get("/api/v1/products", params=SEARCH, headers={"X-API-Key": VALID_KEY})

    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/health", "/docs", "/openapi.json"])
def test_public_endpoints_need_no_key(anon_client: TestClient, path: str) -> None:
    assert anon_client.get(path).status_code == 200


def test_no_configured_keys_rejects_everyone(integration_env: pytest.MonkeyPatch) -> None:
    with start_client(integration_env, "") as client:
        response = client.get("/api/v1/products", params=SEARCH, headers={"X-API-Key": VALID_KEY})

    assert response.status_code == 401
