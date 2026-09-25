"""Application factory and lifespan: creates and closes the shared HTTP and Redis clients."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.v1.products import router as products_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.exceptions import UpstreamUnavailableError
from app.middleware.request_context import RequestContextMiddleware
from app.scrapers.http_client import create_http_client


def create_redis(redis_url: str) -> redis.Redis:
    return redis.from_url(redis_url)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    app.state.settings = settings
    app.state.http_client = create_http_client(settings)
    app.state.redis = create_redis(settings.redis_url)
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
        return JSONResponse(status_code=502, content={"detail": "Upstream service unavailable"})

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
