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
