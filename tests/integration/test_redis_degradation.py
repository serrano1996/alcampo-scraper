"""Spec 007 RF-15, RF-16: a Redis that is down or hung degrades the service, never a 500."""

import logging

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import create_app
from tests.integration.conftest import TEST_API_KEY, load_fixture, mock_alcampo_search
from tests.services.redis_doubles import DOWN, HUNG, BrokenRedis

SEARCH = {"postal_code": "28001", "term": "leche"}


@pytest.mark.parametrize("error", [DOWN, HUNG], ids=["down", "hung"])
def test_search_without_redis_is_served_from_alcampo(
    integration_env: pytest.MonkeyPatch,
    respx_mock,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    integration_env.setattr(main_module, "create_redis", lambda _url, **_: BrokenRedis(error))
    route = mock_alcampo_search(respx_mock, json_body=load_fixture("alcampo_search_leche.json"))

    with TestClient(create_app(), headers={"X-API-Key": TEST_API_KEY}) as client:
        response = client.get("/api/v1/products", params=SEARCH)
        health = client.get("/health")

    assert response.status_code == 200
    assert response.json()["products"]
    assert route.call_count == 1
    assert health.status_code == 200
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("redis unavailable" in message for message in warnings)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
