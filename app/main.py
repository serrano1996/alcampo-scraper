"""Application factory and lifespan: creates and closes the shared HTTP and Redis clients."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.products import router as products_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.exceptions import (
    PostalCodeNotServedError,
    UpstreamThrottledError,
    UpstreamUnavailableError,
)
from app.middleware.request_context import RequestContextMiddleware
from app.scrapers.http_client import create_http_client
from app.services.in_flight import InFlightSearches
from app.services.rate_limiter import LocalRateLimiter
from app.services.waf_cooldown import LocalCooldown

logger = logging.getLogger(__name__)


def create_redis(redis_url: str, *, timeout_seconds: float) -> redis.Redis:
    """Redis client with connect and per-operation timeouts (spec 007 RF-14).

    Without them a hung (not down) Redis would hang every request forever,
    while `/health`, which never touches Redis, kept reporting healthy.
    """
    return redis.from_url(
        redis_url, socket_connect_timeout=timeout_seconds, socket_timeout=timeout_seconds
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    if not settings.api_keys:
        # Fail closed, but loudly: otherwise "everything returns 401" is a mystery
        # after a deploy (spec 004 RF-8, RF-9, plan-D6).
        logger.warning("no API_KEYS configured: every request to /api/v1 will be rejected")
    app.state.settings = settings
    app.state.http_client = create_http_client(settings)
    app.state.redis = create_redis(
        settings.redis_url, timeout_seconds=settings.redis_timeout_seconds
    )
    # Per process, not per request: it must outlive every search (spec 008 plan-D2).
    app.state.in_flight = InFlightSearches()
    # Local fallbacks while Redis is unavailable: per process, so they outlive
    # the per-request limiter and repository (spec 007 RF-15, plan-D10).
    app.state.rate_limit_fallback = LocalRateLimiter()
    app.state.cooldown_fallback = LocalCooldown()
    try:
        yield
    finally:
        await app.state.http_client.aclose()
        await app.state.redis.aclose()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.include_router(products_router)
    # Added last so it stays the outermost middleware (spec 003 plan-D6).
    app.add_middleware(RequestContextMiddleware)

    @app.exception_handler(UpstreamUnavailableError)
    async def upstream_unavailable_handler(
        request: Request, exc: UpstreamUnavailableError
    ) -> JSONResponse:
        postal_code = request.query_params.get("postal_code")
        term = request.query_params.get("term")
        if isinstance(exc, UpstreamThrottledError):
            # Foreseen and managed (WAF cooldown, outbound rate limit): the
            # actionable ERROR was the challenge itself (spec 003 RF-11); one per
            # rejected search would flood the logs (RF-12, spec 008 plan-D5).
            logger.warning(
                "search throttled reason=%r postal_code=%r term=%r",
                exc.reason,
                postal_code,
                term,
            )
        else:
            logger.error(
                "upstream unavailable reason=%r postal_code=%r term=%r",
                exc.reason,
                postal_code,
                term,
            )
        # The reason is for logs only: the body never carries it (spec 001 RF-17).
        return JSONResponse(status_code=502, content={"detail": "Upstream service unavailable"})

    @app.exception_handler(PostalCodeNotServedError)
    async def postal_code_not_served_handler(
        request: Request, exc: PostalCodeNotServedError
    ) -> JSONResponse:
        # A normal answer, not a failure: INFO, and our own detail, never
        # Alcampo's text (spec 007 RF-4, spec-D8). Same contract as Mercadona.
        logger.info("postal code not served postal_code=%r", exc.postal_code)
        return JSONResponse(
            status_code=404, content={"detail": "Postal code not served by Alcampo"}
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
