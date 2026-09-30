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


class UpstreamThrottledError(UpstreamUnavailableError):
    """Parent of the foreseen, managed degradations: we chose not to call Alcampo.

    The 502 handler logs the whole family as a WARNING, not an ERROR, so a
    cooldown or an exhausted rate limit does not flood the logs (spec 003 RF-12,
    spec 008 plan-D5). The actionable ERROR is the challenge that caused them.
    """


class CooldownActiveError(UpstreamThrottledError):
    """Raised when a search is rejected because the WAF cooldown is active.

    A foreseen, managed degradation: the 502 handler logs it as a WARNING, not an
    ERROR, so a cooldown does not flood the logs (spec 003 RF-12, spec-D2).
    """


class OutboundRateLimitedError(UpstreamThrottledError):
    """Raised when the global outbound rate limit towards Alcampo is exhausted.

    No request is sent: staying under the limit is what avoids a WAF block that
    would take the whole service down for minutes (spec 008 RF-3, RF-4). Being a
    subclass, it still maps to the standard 502.
    """


class PostalCodeNotServedError(AlcampoScraperError):
    """Raised when Alcampo does not serve a postal code: it does not exist or is not deliverable.

    Not an upstream failure: Alcampo answered, the answer is "no". It maps to a
    404 with our own detail, never Alcampo's text (spec 007 RF-4, spec-D8).
    """

    def __init__(self, postal_code: str) -> None:
        super().__init__(postal_code)
        self.postal_code = postal_code


class RegionResolutionLimitedError(UpstreamThrottledError):
    """Raised when resolving a new postal code would exceed its own strict limit.

    Creating a delivery destination is the step Alcampo's WAF punishes (Fase 0
    §5), so new resolutions have a limit of their own, stricter than the global
    one (spec 007 RF-8). A foreseen degradation: WARNING and the standard 502.
    """
