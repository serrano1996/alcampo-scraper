"""Integration harness: real app + real `lifespan`, fakeredis and respx (plan-D11).

`create_redis` is patched to return a brand-new `FakeAsyncRedis()` per test, so
no state leaks between tests even though the app's `lifespan` calls it as if
it were talking to a real Redis instance.
"""

import json
from pathlib import Path

import fakeredis
import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import create_app

ALCAMPO_BASE_URL = "https://alcampo.test"
SEARCH_URL = f"{ALCAMPO_BASE_URL}/api/webproductpagews/v6/product-pages/search"
FIXTURES = Path(__file__).parents[1] / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("ALCAMPO_BASE_URL", ALCAMPO_BASE_URL)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("RETRY_BASE_DELAY", "0")
    monkeypatch.setattr(main_module, "create_redis", lambda _url: fakeredis.FakeAsyncRedis())

    with TestClient(create_app()) as test_client:
        yield test_client


def mock_alcampo_search(
    respx_mock: respx.MockRouter,
    *,
    json_body: dict | None = None,
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> respx.Route:
    """Register the single mocked route a search hits, returning its `respx.Route`."""
    return respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(status_code, json=json_body, headers=headers or {})
    )
