"""Per-request id, start/end log lines and the `X-Request-ID` response header.

Registered as the outermost middleware (plan-D6), so every request, including
`422`s from validation and future `401`s from auth, is traced.
"""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"

logger = logging.getLogger(__name__)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Spec 003 RF-3, RF-5, RF-5b, RF-18."""

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
            dict(request.query_params),
        )
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            logger.info(
                "request finished status=%d duration_ms=%.1f",
                response.status_code,
                (time.perf_counter() - started_at) * 1000,
            )
            return response
        finally:
            request_id_var.reset(token)
