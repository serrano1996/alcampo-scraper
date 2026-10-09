import logging
import re
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

from app.main import create_app
from tests.integration.conftest import (
    SEARCH_URL,
    load_fixture,
    mock_alcampo_search,
    mock_region_chain,
)


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
    assert "('term', 'leche')" in started.getMessage()
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


def test_unhandled_exception_returns_500_logged_without_its_message(
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
    # Spec 015 RF-10 (F10): type and frames, never the message, which can carry
    # an Alcampo body fragment or a URL with the client's term.
    assert error.getMessage().startswith("unhandled error type=RuntimeError frames=")
    assert "boom" in error.getMessage()  # the frame that raised
    assert "secret detail" not in caplog.text
    assert error.exc_info is None
    assert error.request_id == response.headers["X-Request-ID"]
    [finished] = request_records(caplog, "request finished")
    assert "status=500" in finished.getMessage()


def app_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name.startswith("app.")]


def test_every_log_line_of_a_failing_request_shares_its_request_id(
    client: TestClient, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    mock_alcampo_search(respx_mock, status_code=503)

    response = client.get("/api/v1/products", params=SEARCH)

    assert response.status_code == 502
    records = app_records(caplog)
    levels = [r.levelname for r in records]
    assert levels.count("WARNING") == 2  # two retries
    assert levels.count("ERROR") == 2  # retries exhausted + 502 handler
    assert {r.request_id for r in records} == {response.headers["X-Request-ID"]}


def test_alcampo_cookies_never_reach_the_logs(
    client: TestClient, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    mock_region_chain(respx_mock)
    route = respx_mock.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            503,
            headers=[
                ("Set-Cookie", "VISITORID=secret-cookie-value; Path=/"),
                ("Set-Cookie", "global_sid=secret-sid-value; Path=/"),
            ],
        )
    )

    client.get("/api/v1/products", params=SEARCH)

    assert route.called  # otherwise this would pass without testing anything
    assert "secret-cookie-value" not in caplog.text
    assert "secret-sid-value" not in caplog.text


def test_client_input_cannot_forge_log_lines(
    client: TestClient, respx_mock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    mock_alcampo_search(respx_mock, status_code=503)

    client.get(
        "/api/v1/products", params={"postal_code": "28001", "term": "leche\nERROR fake injected"}
    )

    assert all("\n" not in r.getMessage() for r in app_records(caplog))
    [handler_error] = [r for r in app_records(caplog) if r.name == "app.main"]
    # The newline must appear escaped (backslash + n), never as a real line break.
    assert r"leche\nERROR fake injected" in handler_error.getMessage()


def test_a_streamed_response_is_finished_only_after_its_body(
    integration_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # spec 006 RF-5: BaseHTTPMiddleware logged "request finished" when the
    # response started, before a streamed body was sent; the ASGI middleware
    # logs it once the body is done, with the same request id throughout.
    caplog.set_level(logging.INFO)
    app = create_app()
    body_logger = logging.getLogger("app.test_stream")

    async def chunks() -> AsyncIterator[bytes]:
        yield b"first "
        body_logger.info("streaming the second chunk")
        yield b"second"

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        return StreamingResponse(chunks(), media_type="text/plain")

    with TestClient(app) as client:
        response = client.get("/stream")

    assert response.text == "first second"
    request_id = response.headers["X-Request-ID"]
    lines = [
        r.getMessage()
        for r in caplog.records
        if r.name in {"app.test_stream", "app.middleware.request_context"}
    ]
    assert lines.index("streaming the second chunk") < next(
        i for i, line in enumerate(lines) if line.startswith("request finished")
    )
    ours = [r for r in caplog.records if r.name == "app.test_stream"]
    assert [r.request_id for r in ours] == [request_id]


def test_an_error_after_the_response_started_is_logged_and_re_raised(
    integration_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Spec 012 RF-6: the 200 is already on its way, so it cannot become a 500.
    caplog.set_level(logging.INFO)
    app = create_app()

    async def chunks() -> AsyncIterator[bytes]:
        yield b"first chunk"
        raise RuntimeError("broken stream")

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        return StreamingResponse(chunks())

    with TestClient(app) as client, pytest.raises(RuntimeError, match="broken stream"):
        client.get("/stream")

    [error] = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert error.getMessage().startswith("unhandled error type=RuntimeError frames=")
    assert "broken stream" not in caplog.text  # spec 015 RF-10
    assert error.request_id != "-"
    [finished] = request_records(caplog, "request finished")
    assert "status=200" in finished.getMessage()  # no 500 sent after the 200
