import json
import logging
from pathlib import Path

import fakeredis
import httpx
import pytest
import respx

from app.core.config import Settings
from app.exceptions import OutboundRateLimitedError, UpstreamUnavailableError
from app.models.alcampo import AlcampoSearchResponse
from app.scrapers.alcampo_search import SEARCH_PATH, AlcampoSearchScraper
from app.services.rate_limiter import OutboundRateLimiter
from tests.services.outbound_doubles import RecordingGate, gate_for

FIXTURES = Path(__file__).parents[1] / "fixtures"
SEARCH_URL = "https://alcampo.test/api/webproductpagews/v6/product-pages/search"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_scraper(
    *, max_attempts: int = 3, rate_limiter: OutboundRateLimiter | None = None
) -> tuple[AlcampoSearchScraper, httpx.AsyncClient]:
    settings = Settings(
        _env_file=None,
        alcampo_base_url="https://alcampo.test",
        redis_url="redis://localhost:6379/0",
        retry_max_attempts=max_attempts,
        retry_base_delay=0,
        retry_jitter_max_s=0,
    )
    client = httpx.AsyncClient(base_url=settings.alcampo_base_url)
    scraper = AlcampoSearchScraper(settings=settings, gate=gate_for(rate_limiter))
    return scraper, client


@respx.mock
async def test_search_calls_alcampo_exactly_once_with_expected_params() -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("alcampo_search_leche.json"))
    )
    scraper, client = make_scraper()

    try:
        await scraper.search("leche", client=client)
    finally:
        await client.aclose()

    assert route.call_count == 1
    request = route.calls.last.request
    query = httpx.QueryParams(request.url.query)
    assert query["q"] == "leche"
    assert query["tag"] == "web"
    assert query["maxPageSize"] == "50"
    assert query["maxProductsToDecorate"] == "50"


@respx.mock
async def test_search_returns_parsed_response() -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("alcampo_search_leche.json"))
    )
    scraper, client = make_scraper()

    try:
        response = await scraper.search("leche", client=client)
    finally:
        await client.aclose()

    assert isinstance(response, AlcampoSearchResponse)
    assert len(response.product_groups[0].decorated_products) == 3


@respx.mock
async def test_search_raises_on_non_json_body() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, html="<html>not json</html>"))
    scraper, client = make_scraper()

    try:
        with pytest.raises(UpstreamUnavailableError):
            await scraper.search("leche", client=client)
    finally:
        await client.aclose()


@respx.mock
async def test_search_raises_on_json_without_expected_shape() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"foo": 1}))
    scraper, client = make_scraper()

    try:
        with pytest.raises(UpstreamUnavailableError):
            await scraper.search("leche", client=client)
    finally:
        await client.aclose()


@respx.mock
async def test_search_raises_on_persistent_5xx() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(503))
    scraper, client = make_scraper(max_attempts=2)

    try:
        with pytest.raises(UpstreamUnavailableError):
            await scraper.search("leche", client=client)
    finally:
        await client.aclose()


@respx.mock
async def test_search_raises_on_persistent_transport_error_not_httpx() -> None:
    respx.get(SEARCH_URL).mock(side_effect=httpx.ConnectError("boom"))
    scraper, client = make_scraper(max_attempts=2)

    try:
        with pytest.raises(UpstreamUnavailableError):
            await scraper.search("leche", client=client)
    finally:
        await client.aclose()


async def test_search_passes_the_configured_jitter_to_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    async def fake_send_with_retry(send, **kwargs):
        captured.update(kwargs)
        return httpx.Response(200, json=load_fixture("alcampo_search_leche.json"))

    monkeypatch.setattr("app.scrapers.alcampo_search.send_with_retry", fake_send_with_retry)
    settings = Settings(
        _env_file=None,
        alcampo_base_url="https://alcampo.test",
        redis_url="redis://localhost:6379/0",
        retry_jitter_max_s=0.25,
    )
    async with httpx.AsyncClient(base_url=settings.alcampo_base_url) as client:
        await AlcampoSearchScraper(settings=settings, gate=gate_for()).search(
            "leche", client=client
        )

    assert captured["jitter_max"] == 0.25
    assert SEARCH_PATH in captured["url"]
    assert "q=leche" in captured["url"]


def scraper_errors(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == "app.scrapers.alcampo_search" and r.levelno == logging.ERROR
    ]


@respx.mock
async def test_non_json_body_is_logged_as_invalid_json(caplog: pytest.LogCaptureFixture) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, html="<html>not json</html>"))
    scraper, client = make_scraper()

    try:
        with pytest.raises(UpstreamUnavailableError):
            await scraper.search("leche", client=client)
    finally:
        await client.aclose()

    [error] = scraper_errors(caplog)
    assert "invalid JSON" in error.getMessage()
    assert SEARCH_PATH in error.getMessage()


@respx.mock
async def test_unexpected_shape_is_logged_as_unexpected_schema(
    caplog: pytest.LogCaptureFixture,
) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"foo": 1}))
    scraper, client = make_scraper()

    try:
        with pytest.raises(UpstreamUnavailableError):
            await scraper.search("leche", client=client)
    finally:
        await client.aclose()

    [error] = scraper_errors(caplog)
    assert "unexpected schema" in error.getMessage()
    assert SEARCH_PATH in error.getMessage()


# --- spec 008: outbound rate limit ---------------------------------------------


@respx.mock
async def test_exhausted_limit_sends_nothing_to_alcampo() -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={}))
    limiter = OutboundRateLimiter(fakeredis.FakeAsyncRedis(), limit=1, window_seconds=60)
    await limiter.acquire()  # someone else used the only slot
    scraper, client = make_scraper(rate_limiter=limiter)

    async with client:
        with pytest.raises(OutboundRateLimitedError):
            await scraper.search("leche", client=client)

    assert route.call_count == 0


@respx.mock
async def test_limit_exhausted_between_attempts_stops_the_retries() -> None:
    # spec 008 RF-5: every attempt takes a slot; retries stop when none is left.
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(503))
    limiter = OutboundRateLimiter(fakeredis.FakeAsyncRedis(), limit=1, window_seconds=60)
    scraper, client = make_scraper(max_attempts=3, rate_limiter=limiter)

    async with client:
        with pytest.raises(OutboundRateLimitedError):
            await scraper.search("leche", client=client)

    assert route.call_count == 1


# --- spec 007 RF-9: search with the region's session ---------------------------


@respx.mock
async def test_search_uses_the_client_of_the_region_session() -> None:
    # The region lives in the session cookies: the search must go out with them.
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("alcampo_search_leche.json"))
    )
    scraper, default_client = make_scraper()
    async with (
        default_client,
        httpx.AsyncClient(
            base_url="https://alcampo.test", cookies={"global_sid": "telde-session"}
        ) as telde_client,
    ):
        await scraper.search("leche", client=telde_client)

    assert "global_sid=telde-session" in route.calls.last.request.headers["cookie"]


# --- spec 010: every request goes through the outbound gate ---------------------


@respx.mock
async def test_the_search_is_announced_and_its_answer_reported_to_the_gate() -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("alcampo_search_leche.json"))
    )
    gate = RecordingGate()
    settings = Settings(
        _env_file=None, alcampo_base_url="https://alcampo.test", redis_url="redis://x"
    )
    async with httpx.AsyncClient(base_url=settings.alcampo_base_url) as client:
        await AlcampoSearchScraper(settings=settings, gate=gate).search("leche", client=client)

    assert gate.kinds == ["search"]
    assert gate.answers == [("search", SEARCH_PATH, 200)]
