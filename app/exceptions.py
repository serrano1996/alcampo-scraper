"""Domain exceptions. Must never expose types from httpx or any other transport library."""


class AlcampoScraperError(Exception):
    """Base class for every domain error raised by this service."""


class UpstreamUnavailableError(AlcampoScraperError):
    """Raised when Alcampo cannot be reached or its response cannot be trusted.

    Covers exhausted retries, non-retryable 4xx, WAF challenges and malformed
    upstream bodies. `reason` is for internal logging only and must never be
    forwarded to the API response.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class UpstreamBlockedError(UpstreamUnavailableError):
    """Raised when Alcampo's AWS WAF answers with a challenge.

    The egress IP stays blocked for minutes (Fase 0 §5), so callers use this
    subtype to start a cooldown (spec 002 RF-15). Being a subclass, it still
    maps to the standard 502 through the `UpstreamUnavailableError` handler.
    """
