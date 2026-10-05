import io
import logging
import re
from collections.abc import Iterator

import httpx
import pytest
import respx

from app.core.logging import PageTokenRedactor, configure_logging, request_id_var

LINE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} (?P<level>[A-Z]+) "
    r"\[(?P<request_id>[^\]]+)\] (?P<logger>\S+): (?P<message>.*)$"
)


@pytest.fixture(autouse=True)
def restore_logging_state() -> Iterator[None]:
    """`configure_logging` mutates process-wide state: undo it after each test (plan R1)."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    factory = logging.getLogRecordFactory()
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    logging.setLogRecordFactory(factory)


def lines(buffer: io.StringIO) -> list[str]:
    return buffer.getvalue().splitlines()


def test_line_has_timestamp_level_request_id_logger_and_message() -> None:
    buffer = io.StringIO()
    configure_logging("INFO", stream=buffer)

    logging.getLogger("x").info("hola")

    [line] = lines(buffer)
    match = LINE.match(line)
    assert match is not None, line
    assert match.group("level", "request_id", "logger", "message") == ("INFO", "-", "x", "hola")


def test_request_id_from_context_is_on_every_record(caplog: pytest.LogCaptureFixture) -> None:
    buffer = io.StringIO()
    configure_logging("INFO", stream=buffer)
    token = request_id_var.set("abc")
    try:
        logging.getLogger("x").info("hola")
    finally:
        request_id_var.reset(token)

    assert "[abc]" in buffer.getvalue()
    assert caplog.records[-1].request_id == "abc"


def test_configuring_twice_does_not_duplicate_lines() -> None:
    buffer = io.StringIO()
    configure_logging("INFO", stream=buffer)
    configure_logging("INFO", stream=buffer)

    logging.getLogger("x").info("hola")

    assert len(lines(buffer)) == 1


def test_caplog_keeps_capturing_after_configuring(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging("INFO", stream=io.StringIO())

    logging.getLogger("x").info("captured by caplog")

    assert "captured by caplog" in caplog.text


def test_level_filters_lower_records() -> None:
    buffer = io.StringIO()
    configure_logging("WARNING", stream=buffer)

    logging.getLogger("x").info("hidden")
    logging.getLogger("x").warning("shown")

    assert [LINE.match(line).group("message") for line in lines(buffer)] == ["shown"]


# --- spec 012 RF-7: page tokens never reach the logs ----------------------------


@pytest.fixture
def httpx_logger() -> Iterator[logging.Logger]:
    """The `httpx` logger, with its filters restored afterwards (process-wide state)."""
    logger = logging.getLogger("httpx")
    filters = list(logger.filters)
    yield logger
    logger.filters[:] = filters


@respx.mock
async def test_the_page_token_is_redacted_from_httpx_request_lines(
    httpx_logger: logging.Logger, caplog: pytest.LogCaptureFixture
) -> None:
    configure_logging("INFO", stream=io.StringIO())
    respx.get("https://alcampo.test/search").mock(return_value=httpx.Response(200))

    # A real httpx request, so the test follows httpx's own log format (plan R2).
    async with httpx.AsyncClient() as client:
        await client.get(
            "https://alcampo.test/search", params={"q": "leche", "pageToken": "tok-secreto"}
        )

    [line] = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    assert "tok-secreto" not in line
    assert "pageToken=<redacted>" in line
    assert line.startswith("HTTP Request: GET https://alcampo.test/search?q=leche&")
    assert "200" in line


def test_configuring_twice_installs_one_redactor(httpx_logger: logging.Logger) -> None:
    # Whatever ran before in this process (every app lifespan configures logging).
    configure_logging("INFO", stream=io.StringIO())
    configure_logging("INFO", stream=io.StringIO())

    redactors = [f for f in httpx_logger.filters if isinstance(f, PageTokenRedactor)]
    assert len(redactors) == 1
