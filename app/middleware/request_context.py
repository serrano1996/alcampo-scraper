"""Per-request id, start/end log lines and the `X-Request-ID` response header.

Registered as the outermost middleware (spec 003 plan-D6), so every request,
including `422`s from validation and `401`s from auth, is traced.

A pure ASGI middleware (spec 006 RF-5, plan-D2), not `BaseHTTPMiddleware`: that
one logged "request finished" when the response started, before a streamed
body was sent, and ran the endpoint in a separate task. Here the end line is
written once the whole response has gone out.
"""

import logging
import time
import uuid

from starlette.datastructures import MutableHeaders, QueryParams
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"

# Longest param list logged on the start line: a huge query must not bloat the
# logs (spec 015 RF-5).
MAX_PARAMS_LOGGED = 500
# A query param whose name contains any of these is hidden on the start line: a
# client sending its key in the URL by mistake must not leak it into our logs
# (spec 004 RF-14, plan-D7). Judged by the normalised name (trimmed, lower case,
# "-" as "_"), never by the value; broader than the five exact names of spec 004
# since dia-scraper's review found `api-key`, `access_token`, `password` and the
# like leaking (spec 015 RF-4, spec-D2). None of the API's own params
# (postal_code, term, page, page_size) matches.
SECRET_NAME_MARKERS = ("key", "token", "secret", "auth", "pass")
REDACTED = "***"

logger = logging.getLogger(__name__)


def params_for_log(query_string: bytes) -> str:
    """Every param, repeated ones included, redacted, through repr and capped (RF-5)."""
    text = repr(redact_params(QueryParams(query_string).multi_items()))
    if len(text) > MAX_PARAMS_LOGGED:
        return f"{text[:MAX_PARAMS_LOGGED]}...(truncated, {len(text)} chars)"
    return text


def redact_params(params: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """`params` with the values of secret-named params replaced by `***`, order kept."""
    return [(name, REDACTED if _is_secret_name(name) else value) for name, value in params]


def _is_secret_name(name: str) -> bool:
    normalised = name.strip().lower().replace("-", "_")
    return any(marker in normalised for marker in SECRET_NAME_MARKERS)


class RequestContextMiddleware:
    """Spec 003 RF-3, RF-5, RF-5b, RF-6, RF-18; spec 004 RF-11, RF-14."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Always generated here: a client-supplied X-Request-ID is ignored, so
        # external input can never forge log lines (RF-5b, spec-D1).
        request_id = uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started_at = time.perf_counter()
        status = 500
        response_started = False
        # Client-controlled values go through %r so control chars are escaped (RF-18).
        logger.info(
            "request started method=%s path=%r params=%s",
            scope["method"],
            scope["path"],
            # Already a repr: control characters stay escaped (RF-18).
            params_for_log(scope["query_string"]),
        )

        async def send_with_request_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            try:
                await self.app(scope, receive, send_with_request_id)
            except Exception:
                # Handled here, not with app.exception_handler(Exception): that
                # one runs outside this middleware, after the request id is gone
                # and without X-Request-ID on the response (spec 003 plan-D2).
                logger.exception("unhandled error")
                if response_started:
                    raise  # a response already on its way cannot become a 500
                error = JSONResponse({"detail": "Internal server error"}, status_code=500)
                await error(scope, receive, send_with_request_id)
        finally:
            logger.info(
                "request finished status=%d duration_ms=%.1f",
                status,
                (time.perf_counter() - started_at) * 1000,
            )
            request_id_var.reset(token)
