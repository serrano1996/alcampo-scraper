"""Spec 012 RF-1 to RF-3: `/ready` says whether the instance has Redis, as in Mercadona."""

import logging

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import create_app
from tests.services.redis_doubles import DOWN, CountingBrokenRedis

READY = {"status": "ready"}
UNAVAILABLE = {"status": "unavailable", "redis": "unreachable"}


def warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_ready_with_redis(integration_env: pytest.MonkeyPatch, respx_mock) -> None:
    # No X-API-Key, like /health; respx has no routes: Alcampo is never called.
    with TestClient(create_app()) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == READY


def test_not_ready_without_redis_and_still_alive(
    integration_env: pytest.MonkeyPatch, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    redis = CountingBrokenRedis(DOWN)
    integration_env.setattr(main_module, "create_redis", lambda _url, **_: redis)

    with TestClient(create_app()) as client:
        response = client.get("/ready")
        health = client.get("/health")

    assert response.status_code == 503
    assert response.json() == UNAVAILABLE
    assert "localhost" not in response.text  # no Redis URL...
    assert "refused" not in response.text  # ...nor the error
    assert any("redis unavailable" in message for message in warnings(caplog))
    assert health.status_code == 200  # alive: the app serves without Redis (RF-3)


def test_an_open_circuit_answers_without_trying_redis(
    integration_env: pytest.MonkeyPatch, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    redis = CountingBrokenRedis(DOWN)
    integration_env.setattr(main_module, "create_redis", lambda _url, **_: redis)

    with TestClient(create_app()) as client:
        client.get("/ready")  # fails: opens the circuit
        attempts, warned = redis.attempts, len(warnings(caplog))
        again = client.get("/ready")

    assert again.status_code == 503
    assert again.json() == UNAVAILABLE
    # A probe every few seconds must not hammer a dead Redis nor flood the log (plan-D1).
    assert redis.attempts == attempts
    assert len(warnings(caplog)) == warned
