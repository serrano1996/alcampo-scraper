"""Application factory and lifespan: creates and closes the shared clients and services."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.products import router as products_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.state import AppResources
from app.exceptions import (
    PostalCodeNotServedError,
    UpstreamThrottledError,
    UpstreamUnavailableError,
)
from app.middleware.request_context import RequestContextMiddleware
from app.scrapers.alcampo_session import AlcampoSessionClient
from app.scrapers.http_client import create_http_client
from app.services.in_flight import InFlightSearches
from app.services.outbound import OutboundGate, OutboundPacer, TrafficLog
from app.services.rate_limiter import (
    RATE_LIMIT_LONG_KEY,
    LocalRateLimiter,
    OutboundRateLimiter,
)
from app.services.redis_circuit import RedisCircuitBreaker
from app.services.region_repository import RegionMemory, RegionRepository
from app.services.region_service import REGION_RESOLUTIONS_KEY, RegionService
from app.services.region_sessions import RegionSessions
from app.services.waf_cooldown import LocalCooldown, WafCooldownRepository

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
    redis_client = create_redis(settings.redis_url, timeout_seconds=settings.redis_timeout_seconds)
    # Everything below keeps its state in Redis or in process memory, so it is
    # built once per process, never per request: the in-flight registries, the
    # local fallbacks (spec 007 RF-15) and the sessions must outlive each search.
    # One circuit for every Redis-backed repository (spec 007 RF-18, spec-D11).
    circuit = RedisCircuitBreaker(open_seconds=settings.redis_circuit_open_seconds)
    # Every request to Alcampo goes through this gate: spacing, a short and a
    # long window, and the traffic log for challenge breakdowns (spec 010).
    gate = OutboundGate(
        pacer=OutboundPacer(
            min_interval_ms=settings.alcampo_min_interval_ms,
            jitter_ms=settings.alcampo_interval_jitter_ms,
        ),
        short=OutboundRateLimiter(
            redis_client,
            limit=settings.alcampo_rate_limit,
            window_seconds=settings.alcampo_rate_window_seconds,
            fallback=LocalRateLimiter(),
            circuit=circuit,
        ),
        long=OutboundRateLimiter(
            redis_client,
            limit=settings.alcampo_rate_limit_long,
            window_seconds=settings.alcampo_rate_window_long_seconds,
            key=RATE_LIMIT_LONG_KEY,
            fallback=LocalRateLimiter(),
            circuit=circuit,
        ),
        traffic=TrafficLog(),
    )
    cooldown = WafCooldownRepository(redis_client, fallback=LocalCooldown(), circuit=circuit)
    regions = RegionRepository(
        redis_client,
        memory=RegionMemory(),
        ttl_seconds=settings.region_cache_ttl_seconds,
        negative_ttl_seconds=settings.region_negative_cache_ttl_seconds,
        circuit=circuit,
    )

    def new_session() -> AlcampoSessionClient:
        # Its own HTTP client, so its own cookies: one Alcampo session each.
        return AlcampoSessionClient(
            client=create_http_client(settings),
            settings=settings,
            gate=gate,
        )

    region_sessions = RegionSessions(
        new_session=new_session,
        repository=regions,
        max_age_seconds=settings.session_max_age_seconds,
    )
    region_service = RegionService(
        repository=regions,
        new_session=new_session,
        sessions=region_sessions,
        resolution_limiter=OutboundRateLimiter(
            redis_client,
            limit=settings.region_resolution_limit,
            window_seconds=settings.region_resolution_window_seconds,
            key=REGION_RESOLUTIONS_KEY,
            fallback=LocalRateLimiter(),
            circuit=circuit,
        ),
        cooldown=cooldown,
        in_flight=InFlightSearches(),
        settings=settings,
    )
    # One typed object instead of loose attributes (spec 006 RF-1, plan-D1).
    app.state.resources = AppResources(
        settings=settings,
        redis=redis_client,
        redis_circuit=circuit,
        gate=gate,
        cooldown=cooldown,
        in_flight=InFlightSearches(),
        region_sessions=region_sessions,
        region_service=region_service,
    )
    try:
        yield
    finally:
        await region_sessions.aclose()
        await redis_client.aclose()


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
