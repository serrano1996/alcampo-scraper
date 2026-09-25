import json
from pathlib import Path

import httpx
import pytest
import respx

from app.core.config import Settings
from app.exceptions import UpstreamUnavailableError
from app.models.alcampo import AlcampoSearchResponse
from app.scrapers.alcampo_search import DEFAULT_WAREHOUSE, AlcampoSearchScraper

FIXTURES = Path(__file__).parents[1] / "fixtures"
SEARCH_URL = "https://alcampo.test/api/webproductpagews/v6/product-pages/search"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_scraper(*, max_attempts: int = 3) -> tuple[AlcampoSearchScraper, httpx.AsyncClient]:
    settings = Settings(
        _env_file=None,
        alcampo_base_url="https://alcampo.test",
        redis_url="redis://localhost:6379/0",
        retry_max_attempts=max_attempts,
        retry_base_delay=0,
        retry_jitter_max_s=0,
    )
    client = httpx.AsyncClient(base_url=settings.alcampo_base_url)
    scraper = AlcampoSearchScraper(client=client, settings=settings)
    return scraper, client


def test_default_warehouse_is_the_verified_fase_0_region() -> None:
    assert DEFAULT_WAREHOUSE == "5"


@respx.mock
async def test_search_calls_alcampo_exactly_once_with_expected_params() -> None:
    route = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("alcampo_search_leche.json"))
    )
    scraper, client = make_scraper()

    try:
        await scraper.search("leche")
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
        response = await scraper.search("leche")
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
            await scraper.search("leche")
    finally:
        await client.aclose()


@respx.mock
async def test_search_raises_on_json_without_expected_shape() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json={"foo": 1}))
    scraper, client = make_scraper()

    try:
        with pytest.raises(UpstreamUnavailableError):
            await scraper.search("leche")
    finally:
        await client.aclose()


@respx.mock
async def test_search_raises_on_persistent_5xx() -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(503))
    scraper, client = make_scraper(max_attempts=2)

    try:
        with pytest.raises(UpstreamUnavailableError):
            await scraper.search("leche")
    finally:
        await client.aclose()


@respx.mock
async def test_search_raises_on_persistent_transport_error_not_httpx() -> None:
    respx.get(SEARCH_URL).mock(side_effect=httpx.ConnectError("boom"))
    scraper, client = make_scraper(max_attempts=2)

    try:
        with pytest.raises(UpstreamUnavailableError):
            await scraper.search("leche")
    finally:
        await client.aclose()


async def test_search_passes_the_configured_jitter_to_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, float] = {}

    async def fake_send_with_retry(send, *, max_attempts, base_delay, jitter_max):
        captured["jitter_max"] = jitter_max
        return httpx.Response(200, json=load_fixture("alcampo_search_leche.json"))

    monkeypatch.setattr("app.scrapers.alcampo_search.send_with_retry", fake_send_with_retry)
    settings = Settings(
        _env_file=None,
        alcampo_base_url="https://alcampo.test",
        redis_url="redis://localhost:6379/0",
        retry_jitter_max_s=0.25,
    )
    async with httpx.AsyncClient(base_url=settings.alcampo_base_url) as client:
        await AlcampoSearchScraper(client=client, settings=settings).search("leche")

    assert captured == {"jitter_max": 0.25}
