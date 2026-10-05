"""Outbound gates for tests that are not about pacing or limits (spec 010)."""

import fakeredis

from app.services.outbound import OutboundGate, OutboundPacer, TrafficLog
from app.services.rate_limiter import OutboundRateLimiter


def disabled_limiter() -> OutboundRateLimiter:
    return OutboundRateLimiter(fakeredis.FakeAsyncRedis(), limit=0, window_seconds=60)


def gate_for(short: OutboundRateLimiter | None = None) -> OutboundGate:
    """A gate without spacing or long window; `short` is the limit under test, if any."""
    return OutboundGate(
        pacer=OutboundPacer(min_interval_ms=0, jitter_ms=0),
        short=short or disabled_limiter(),
        long=disabled_limiter(),
        traffic=TrafficLog(),
    )


class RecordingGate(OutboundGate):
    """A disabled gate that remembers every request kind and every answer."""

    def __init__(self) -> None:
        disabled = gate_for()
        super().__init__(
            pacer=disabled._pacer,
            short=disabled._short,
            long=disabled._long,
            traffic=disabled.traffic,
        )
        self.kinds: list[str] = []
        self.answers: list[tuple[str, str, int]] = []

    async def before_request(self, kind: str) -> None:
        self.kinds.append(kind)
        await super().before_request(kind)

    def after_response(self, *, kind: str, endpoint: str, status: int) -> None:
        self.answers.append((kind, endpoint, status))
        super().after_response(kind=kind, endpoint=endpoint, status=status)
