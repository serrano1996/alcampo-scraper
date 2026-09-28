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
    """Register the single mocked route a search hits, returning its `respx.Route`."""
    return respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(status_code, json=json_body, headers=headers or {})
    )
