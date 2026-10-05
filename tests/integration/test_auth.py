import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.core.state import resources
from app.main import create_app
from app.models.product import ProductSearchResponse, SearchMetadata
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


def auth_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r for r in caplog.records if r.name == "app.core.security" and r.levelno == logging.WARNING
    ]


def test_missing_key_is_logged_with_the_path(
    anon_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    anon_client.get("/api/v1/products", params=SEARCH)

    [record] = auth_warnings(caplog)
    assert "reason=missing" in record.getMessage()
    assert "'/api/v1/products'" in record.getMessage()


def test_invalid_key_is_logged_without_its_value(
    integration_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # DEBUG everywhere: no level may ever carry a key (spec 004 RF-12).
    integration_env.setenv("LOG_LEVEL", "DEBUG")
    caplog.set_level(logging.DEBUG)
    with start_client(integration_env, VALID_KEY) as client:
        client.get("/api/v1/products", params=SEARCH, headers={"X-API-Key": "wrong-secret-value"})

    [record] = auth_warnings(caplog)
    assert "reason=invalid" in record.getMessage()
    assert "wrong-secret-value" not in caplog.text
    assert VALID_KEY not in caplog.text


def startup_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == "app.main" and r.levelno == logging.WARNING and "API_KEYS" in r.getMessage()
    ]


def test_starting_without_keys_warns_that_everything_will_be_rejected(
    integration_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with start_client(integration_env, " , "):
        pass

    [record] = startup_warnings(caplog)
    assert "/api/v1" in record.getMessage()


def test_starting_with_keys_does_not_warn(
    integration_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with start_client(integration_env, VALID_KEY):
        pass

    assert startup_warnings(caplog) == []


def test_key_sent_in_the_url_is_redacted_from_app_logs(
    anon_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    anon_client.get("/api/v1/products", params={**SEARCH, "api_key": "secret-in-url"})

    # Only the app's own records: the test client's httpx logs its own request URL.
    app_text = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("app."))
    assert "secret-in-url" not in app_text
    assert "'api_key': '***'" in app_text


CACHED_RESPONSE = ProductSearchResponse(
    search=SearchMetadata(
        postal_code="28001",
        term="leche",
        warehouse="5",
        strategy_used="api",
        scraped_at=datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC),
        total_results=0,
        page=1,
        page_size=50,
        total_pages=0,
    ),
    products=[],
)


async def test_rejection_happens_before_cache_cooldown_and_alcampo(
    anon_client: TestClient, respx_mock
) -> None:
    route = mock_alcampo_search(respx_mock, json_body=load_fixture("alcampo_search_leche.json"))
    redis = resources(anon_client.app).redis
    await redis.set("search:5:leche:1:50", CACHED_RESPONSE.model_dump_json())
    await redis.set("waf:cooldown", "1")

    response = anon_client.get("/api/v1/products", params=SEARCH)

    assert response.status_code == 401  # not 200 from cache, not 502 from cooldown
    assert response.json() == UNAUTHORIZED
    assert route.call_count == 0


def test_rejection_takes_precedence_over_validation(anon_client: TestClient) -> None:
    response = anon_client.get(
        "/api/v1/products",
        params={"postal_code": "28001", "term": "   "},
        headers={"X-API-Key": "x"},
    )

    assert response.status_code == 401  # not 422


def test_rejections_are_traced_like_any_request(
    anon_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    response = anon_client.get("/api/v1/products", params=SEARCH)

    request_id = response.headers["X-Request-ID"]
    lines = [
        r.getMessage()
        for r in caplog.records
        if r.name == "app.middleware.request_context" and r.request_id == request_id
    ]
    assert any(line.startswith("request started") for line in lines)
    assert any(line.startswith("request finished status=401") for line in lines)


def test_openapi_declares_the_api_key_requirement(anon_client: TestClient) -> None:
    schema = anon_client.get("/openapi.json").json()

    schemes = schema["components"]["securitySchemes"].values()
    assert {"type": "apiKey", "in": "header", "name": "X-API-Key"} in [
        {k: s[k] for k in ("type", "in", "name")} for s in schemes
    ]
    assert schema["paths"]["/api/v1/products"]["get"]["security"]
