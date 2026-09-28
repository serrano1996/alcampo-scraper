"""Per-request id, start/end log lines and the `X-Request-ID` response header.

Registered as the outermost middleware (plan-D6), so every request, including
`422`s from validation and future `401`s from auth, is traced.
"""

import logging
import time
import uuid
from collections.abc import Mapping

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

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


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Spec 003 RF-3, RF-5, RF-5b, RF-6, RF-18."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # Always generated here: a client-supplied X-Request-ID is ignored, so
        # external input can never forge log lines (RF-5b, spec-D1).
        request_id = uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started_at = time.perf_counter()
        # Client-controlled values go through %r so control chars are escaped (RF-18).
        logger.info(
            "request started method=%s path=%r params=%r",
            request.method,
            request.url.path,
            redact_params(request.query_params),
        )
        try:
            try:
                response = await call_next(request)
            except Exception:
                # Handled here, not with app.exception_handler(Exception): that
                # one runs outside this middleware, after the request id is gone
                # and without X-Request-ID on the response (plan §2, plan-D2).
                logger.exception("unhandled error")
                response = JSONResponse({"detail": "Internal server error"}, status_code=500)
            response.headers[REQUEST_ID_HEADER] = request_id
            logger.info(
                "request finished status=%d duration_ms=%.1f",
                response.status_code,
                (time.perf_counter() - started_at) * 1000,
            )
            return response
        finally:
            request_id_var.reset(token)
