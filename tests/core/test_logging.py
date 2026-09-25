import io
import logging
import re
from collections.abc import Iterator

import pytest

from app.core.logging import configure_logging, request_id_var

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
