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
from collections.abc import Mapping

from starlette.datastructures import MutableHeaders, QueryParams
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"

# Query params whose value is hidden in the start line, matched by name and
# case-insensitively: a client sending its key in the URL by mistake must not
# leak it into our logs (spec 004 RF-14, plan-D7). Values are never inspected.
SECRET_PARAM_NAMES = frozenset({"api_key", "apikey", "x-api-key", "key", "token"})
REDACTED = "***"

logger = logging.getLogger(__name__)


def redact_params(params: Mapping[str, str]) -> dict[str, str]:
    """Copy of `params` with secret-named values replaced by `***`."""
    return {
        name: REDACTED if name.lower() in SECRET_PARAM_NAMES else value
        for name, value in params.items()
    }


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
            "request started method=%s path=%r params=%r",
            scope["method"],
            scope["path"],
            redact_params(QueryParams(scope["query_string"])),
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
