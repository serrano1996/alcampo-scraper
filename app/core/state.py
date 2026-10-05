"""The resources the `lifespan` builds, as one typed object (spec 006 RF-1, plan-D1).

`app.state` is an untyped namespace: a misspelt attribute (`state.redsi`) used
to fail only at run time. Now the lifespan stores a single `AppResources`, and
`resources(app)` is the only place that reads `app.state`, so any other typo is
a `mypy` error.
"""

from dataclasses import dataclass

from redis.asyncio import Redis
from starlette.applications import Starlette

from app.core.config import Settings
from app.models.product import ProductSearchResponse
from app.services.in_flight import InFlightSearches
from app.services.outbound import OutboundGate
from app.services.redis_circuit import RedisCircuitBreaker
from app.services.region_service import RegionService
from app.services.region_sessions import RegionSessions
from app.services.waf_cooldown import WafCooldownRepository


@dataclass(frozen=True)
class AppResources:
    """Everything stateful, built once per process: it must outlive each request."""

    settings: Settings
    redis: Redis
    redis_circuit: RedisCircuitBreaker
    gate: OutboundGate
    cooldown: WafCooldownRepository
    in_flight: InFlightSearches[ProductSearchResponse]
    region_sessions: RegionSessions
    region_service: RegionService


def resources(app: Starlette) -> AppResources:
    """The `AppResources` of a running app; fails clearly outside its `lifespan`."""
    found = getattr(app.state, "resources", None)
    if not isinstance(found, AppResources):
        raise RuntimeError("app resources are only available while its lifespan runs")
    return found
