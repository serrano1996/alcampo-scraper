import logging
import re

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.integration.conftest import load_fixture, mock_alcampo_search


@pytest.mark.parametrize("level", ["ERROR", "DEBUG"])
def test_lifespan_configures_the_root_level_from_settings(
    integration_env: pytest.MonkeyPatch, level: str
) -> None:
    integration_env.setenv("LOG_LEVEL", level)

    with TestClient(create_app()):
        assert logging.getLogger().level == logging.getLevelName(level)


REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
SEARCH = {"postal_code": "28001", "term": "leche"}


def request_records(caplog: pytest.LogCaptureFixture, prefix: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage().startswith(prefix)]


def test_request_is_logged_at_start_and_end_with_one_request_id(
    client: TestClient, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    mock_alcampo_search(respx_mock, json_body=load_fixture("alcampo_search_leche.json"))

    response = client.get("/api/v1/products", params=SEARCH)

    [started] = request_records(caplog, "request started")
    [finished] = request_records(caplog, "request finished")
    assert REQUEST_ID.match(started.request_id)
    assert finished.request_id == started.request_id
    assert "GET" in started.getMessage()
    assert "'/api/v1/products'" in started.getMessage()
    assert "'term': 'leche'" in started.getMessage()
    assert "status=200" in finished.getMessage()
    assert "duration_ms=" in finished.getMessage()
    assert response.headers["X-Request-ID"] == started.request_id


def test_validation_errors_are_logged_and_carry_the_request_id(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    response = client.get("/api/v1/products", params={"postal_code": "28001", "term": "   "})

    assert response.status_code == 422
    [started] = request_records(caplog, "request started")
    [finished] = request_records(caplog, "request finished")
    assert "status=422" in finished.getMessage()
    assert response.headers["X-Request-ID"] == started.request_id == finished.request_id


def test_client_supplied_request_id_is_ignored(
    client: TestClient, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    mock_alcampo_search(respx_mock, json_body=load_fixture("alcampo_search_leche.json"))

    response = client.get("/api/v1/products", params=SEARCH, headers={"X-Request-ID": "abc"})

    [started] = request_records(caplog, "request started")
    assert started.request_id != "abc"
    assert REQUEST_ID.match(response.headers["X-Request-ID"])
    assert response.headers["X-Request-ID"] == started.request_id


def test_each_request_gets_a_new_request_id(client: TestClient, respx_mock) -> None:
    mock_alcampo_search(respx_mock, json_body=load_fixture("alcampo_search_leche.json"))

    first = client.get("/api/v1/products", params=SEARCH)
    second = client.get("/api/v1/products", params=SEARCH)

    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


def test_unhandled_exception_returns_500_logged_with_traceback(
    integration_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    app = create_app()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret detail")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert "secret detail" not in response.text
    [error] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error.exc_info is not None
    assert error.request_id == response.headers["X-Request-ID"]
    [finished] = request_records(caplog, "request finished")
    assert "status=500" in finished.getMessage()
