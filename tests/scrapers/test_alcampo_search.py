import json
from pathlib import Path

import httpx
import respx

from app.core.config import Settings
from app.models.alcampo import AlcampoSearchResponse
from app.scrapers.alcampo_search import DEFAULT_WAREHOUSE, AlcampoSearchScraper

FIXTURES = Path(__file__).parents[1] / "fixtures"
SEARCH_URL = "https://alcampo.test/api/webproductpagews/v6/product-pages/search"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_scraper() -> tuple[AlcampoSearchScraper, httpx.AsyncClient]:
    settings = Settings(
        _env_file=None,
        alcampo_base_url="https://alcampo.test",
        redis_url="redis://localhost:6379/0",
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
