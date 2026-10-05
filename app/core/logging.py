"""Logging configuration: stderr output, a fixed line format and a per-request id.

Two choices differ from mercadona-scraper on purpose, both verified before
the plan was written (spec 003 plan §2):

- The request id reaches every record through a `LogRecord` factory, not a
  `logging.Filter` (plan-D4): a filter would only tag records flowing through
  our own handler, so pytest's `caplog` records would lack it.
- We manage our own handler instead of `logging.basicConfig(force=True)`
  (plan-D3), which would also remove the handler `caplog` relies on.
"""

import logging
import re
import sys
from collections.abc import Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, TextIO

LOG_FORMAT = "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"

# "-" outside an HTTP request (startup, shutdown, background work).
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_installed_factory: Callable[..., logging.LogRecord] | None = None


if TYPE_CHECKING:
    _StreamHandler = logging.StreamHandler[TextIO]
else:  # not subscriptable at runtime on every supported Python
    _StreamHandler = logging.StreamHandler


class _AppHandler(_StreamHandler):
    """Marks the handler installed by `configure_logging`, so it can be replaced alone."""


_PAGE_TOKEN = re.compile(r"(pageToken=)[^&\s\"]+")


class PageTokenRedactor(logging.Filter):
    """Hides Alcampo's page token in httpx's request lines (spec 012 RF-7).

    A logger filter, not a handler one (plan-D2): it runs before any handler,
    `caplog`'s included, so no destination ever sees the token. The rest of the
    line stays: it is how spacing and traffic are measured in manual checks.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "pageToken=" in message:
            record.msg = _PAGE_TOKEN.sub(r"\1<redacted>", message)
            record.args = ()
        return True


def _install_request_id_factory() -> None:
    global _installed_factory
    current = logging.getLogRecordFactory()
    if current is _installed_factory:
        return

    def factory(*args: object, **kwargs: object) -> logging.LogRecord:
        record = current(*args, **kwargs)
        record.request_id = request_id_var.get()
        return record

    logging.setLogRecordFactory(factory)
    _installed_factory = factory


def configure_logging(level: str, *, stream: TextIO | None = None) -> None:
    """Configure the root logger (spec 003 RF-1). Safe to call more than once."""
    _install_request_id_factory()

    root = logging.getLogger()
    for handler in [h for h in root.handlers if isinstance(h, _AppHandler)]:
        root.removeHandler(handler)

    handler = _AppHandler(sys.stderr if stream is None else stream)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    root.addHandler(handler)
    root.setLevel(level)

    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, PageTokenRedactor) for f in httpx_logger.filters):
        httpx_logger.addFilter(PageTokenRedactor())
