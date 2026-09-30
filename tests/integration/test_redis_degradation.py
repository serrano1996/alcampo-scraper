"""Spec 007 RF-15, RF-16: a Redis that is down or hung degrades the service, never a 500."""

import logging

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import create_app
from tests.integration.conftest import TEST_API_KEY, load_fixture, mock_alcampo_search
from tests.services.redis_doubles import DOWN, HUNG, BrokenRedis, CountingBrokenRedis

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


def test_after_the_first_failure_searches_skip_redis(
    integration_env: pytest.MonkeyPatch, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    # spec 007 RF-18: without the circuit each search waited ~4 Redis timeouts
    # (~9 s in the T13 manual check). Now only the first failure costs one.
    redis = CountingBrokenRedis(DOWN)
    integration_env.setattr(main_module, "create_redis", lambda _url, **_: redis)
    mock_alcampo_search(respx_mock, json_body=load_fixture("alcampo_search_leche.json"))

    with TestClient(create_app(), headers={"X-API-Key": TEST_API_KEY}) as client:
        first = client.get("/api/v1/products", params=SEARCH)
        attempts_after_first = redis.attempts
        second = client.get("/api/v1/products", params={**SEARCH, "term": "agua"})

    assert (first.status_code, second.status_code) == (200, 200)
    assert attempts_after_first == 1  # the first failure opened the circuit
    assert redis.attempts == 1  # the second search did not touch Redis at all
    circuit_warnings = [
        r
        for r in caplog.records
        if r.name == "app.services.redis_circuit" and r.levelno == logging.WARNING
    ]
    assert len(circuit_warnings) == 1
