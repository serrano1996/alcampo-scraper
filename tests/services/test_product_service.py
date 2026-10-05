import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime

import fakeredis
import pytest

from app.core.config import Settings
from app.exceptions import (
    CooldownActiveError,
    PageOutOfRangeError,
    PostalCodeNotServedError,
    UpstreamBlockedError,
    UpstreamUnavailableError,
)
from app.models.alcampo import AlcampoSearchResponse
from app.models.product import ProductQuery
from app.services.in_flight import InFlightSearches
from app.services.outbound import TrafficLog
from app.services.product_service import ProductService
from app.services.region_repository import Region
from app.services.search_cache import SearchCacheRepository
from app.services.waf_cooldown import WAF_COOLDOWN_KEY, WafCooldownRepository
from tests.services.pagination_doubles import ChainedScraper

FIXED_NOW = datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC)

RAW_PRODUCT = {
    "retailerProductId": "54180",
    "name": "AUCHAN Leche semidesnatada de vaca 6 x 1l Producto Alcampo.",
    "price": {"amount": "5.28", "currency": "EUR"},
    "unitPrice": {"price": {"amount": "0.88", "currency": "EUR"}, "unitName": "PER_LITRE"},
    "categoryPath": ["Leche, Huevos, Lácteos", "Leche", "Leche semidesnatada"],
    "image": {"src": "https://img.test/54180.jpg"},  # required since spec 009 RF-11
}


class FakeScraper:
    def __init__(self, response: AlcampoSearchResponse) -> None:
        self.response = response
        self.calls: list[str] = []

    async def search(
        self,
        term: str,
        *,
        client: object = None,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> AlcampoSearchResponse:
        self.calls.append(term)
        return self.response


class FailingScraper:
    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls: list[str] = []

    async def search(
        self,
        term: str,
        *,
        client: object = None,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> AlcampoSearchResponse:
        self.calls.append(term)
        raise self.error


def make_raw_response(*, has_products: bool) -> AlcampoSearchResponse:
    products = [RAW_PRODUCT] if has_products else []
    return AlcampoSearchResponse.model_validate(
        {"productGroups": [{"decoratedProducts": products}]}
    )


class HangingScraper:
    """Alcampo that never answers; records whether the wait was cancelled."""

    def __init__(self) -> None:
        self.cancelled = False

    async def search(
        self,
        term: str,
        *,
        client: object = None,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> AlcampoSearchResponse:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


class GatedScraper:
    """Scraper that answers (or fails) only once `gate` is set."""

    def __init__(self, response: AlcampoSearchResponse, error: Exception | None = None) -> None:
        self.gate = asyncio.Event()
        self.response = response
        self.error = error
        self.calls: list[str] = []

    async def search(
        self,
        term: str,
        *,
        client: object = None,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> AlcampoSearchResponse:
        self.calls.append(term)
        await self.gate.wait()
        if self.error is not None:
            raise self.error
        return self.response


# Existing tests keep the region of spec 001 ("5"), so their `search:5:...`
# cache keys do not change (spec 007 plan §6).
VAGUADA = Region(region_id="vaguada-uuid", retailer_region_id="5", delivery_destination_id="d5")
TELDE = Region(region_id="telde-uuid", retailer_region_id="32", delivery_destination_id="d32")


class FakeRegions:
    """Postal code -> region without the chain; a mapping or an error per code."""

    def __init__(self, regions: dict[str, Region | Exception] | None = None) -> None:
        self.regions = regions or {}

    async def region_for(self, postal_code: str) -> Region:
        region = self.regions.get(postal_code, VAGUADA)
        if isinstance(region, Exception):
            raise region
        return region


class FakeSession:
    def __init__(self, region: Region) -> None:
        self.client = f"client-of-{region.retailer_region_id}"
        self.cursors: dict[tuple[str, int, int], str] = {}


class FakeSessions:
    def __init__(self) -> None:
        self.asked: list[str] = []

    async def get(self, region: Region) -> FakeSession:
        self.asked.append(region.retailer_region_id)
        return FakeSession(region)


def make_service(
    scraper: FakeScraper | FailingScraper | HangingScraper | GatedScraper,
    redis: fakeredis.FakeAsyncRedis,
    *,
    clock: Callable[[], datetime] = lambda: FIXED_NOW,
    waf_cooldown_seconds: int = 180,
    search_timeout_seconds: float = 15,
    regions: FakeRegions | None = None,
    sessions: FakeSessions | None = None,
    traffic: TrafficLog | None = None,
) -> tuple[ProductService, SearchCacheRepository]:
    settings = Settings(
        _env_file=None,
        alcampo_base_url="https://alcampo.test",
        redis_url="redis://localhost:6379/0",
        waf_cooldown_seconds=waf_cooldown_seconds,
        search_timeout_seconds=search_timeout_seconds,
    )
    cache = SearchCacheRepository(redis)
    service = ProductService(
        scraper=scraper,
        cache=cache,
        cooldown=WafCooldownRepository(redis),
        in_flight=InFlightSearches(),
        regions=regions or FakeRegions(),
        sessions=sessions or FakeSessions(),
        traffic=traffic or TrafficLog(),
        settings=settings,
        clock=clock,
    )
    return service, cache


@pytest.fixture
def redis() -> fakeredis.FakeAsyncRedis:
    return fakeredis.FakeAsyncRedis()


async def test_miss_calls_the_scraper_once(redis: fakeredis.FakeAsyncRedis) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)

    await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert scraper.calls == ["leche"]


async def test_miss_returns_expected_metadata(redis: fakeredis.FakeAsyncRedis) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)

    result = await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert result.search.warehouse == "5"
    assert result.search.strategy_used == "api"
    assert result.search.scraped_at == FIXED_NOW
    assert result.search.total_results == 1
    assert result.search.postal_code == "28001"
    assert result.search.term == "leche"
    assert len(result.products) == 1


async def test_miss_stores_the_response_in_cache(redis: fakeredis.FakeAsyncRedis) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, cache = make_service(scraper, redis)

    await service.search(ProductQuery(postal_code="28001", term="leche"))

    cached = await cache.get(warehouse="5", term="leche", page=1, page_size=50)
    assert cached is not None
    assert cached.search.total_results == 1


async def test_empty_search_returns_empty_products_and_is_cached(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scraper = FakeScraper(make_raw_response(has_products=False))
    service, cache = make_service(scraper, redis)

    result = await service.search(ProductQuery(postal_code="28001", term="xqzwvkjhgf"))

    assert result.products == []
    assert result.search.total_results == 0
    cached = await cache.get(warehouse="5", term="xqzwvkjhgf", page=1, page_size=50)
    assert cached is not None


async def test_cache_hit_does_not_call_the_scraper(redis: fakeredis.FakeAsyncRedis) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)
    await service.search(ProductQuery(postal_code="28001", term="leche"))
    scraper.calls.clear()

    await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert scraper.calls == []


async def test_cache_hit_rewrites_the_requested_postal_code(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)
    await service.search(ProductQuery(postal_code="28001", term="leche"))

    result = await service.search(ProductQuery(postal_code="08001", term="leche"))

    assert result.search.postal_code == "08001"


async def test_cache_hit_keeps_the_original_scraped_at(redis: fakeredis.FakeAsyncRedis) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    clock_values = iter(
        [datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC), datetime(2026, 9, 24, 11, 0, 0, tzinfo=UTC)]
    )
    service, _ = make_service(scraper, redis, clock=lambda: next(clock_values))
    first = await service.search(ProductQuery(postal_code="28001", term="leche"))

    second = await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert second.search.scraped_at == first.search.scraped_at


async def test_scraper_error_propagates_and_nothing_is_cached(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scraper = FailingScraper(UpstreamUnavailableError("boom"))
    service, cache = make_service(scraper, redis)

    with pytest.raises(UpstreamUnavailableError):
        await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert await cache.get(warehouse="5", term="leche", page=1, page_size=50) is None


async def test_blocked_scraper_activates_the_cooldown_and_propagates(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    service, _ = make_service(FailingScraper(UpstreamBlockedError("waf")), redis)

    with pytest.raises(UpstreamBlockedError):
        await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert await WafCooldownRepository(redis).is_active() is True
    assert 179 <= await redis.ttl(WAF_COOLDOWN_KEY) <= 180


async def test_plain_upstream_error_does_not_activate_the_cooldown(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    service, _ = make_service(FailingScraper(UpstreamUnavailableError("503")), redis)

    with pytest.raises(UpstreamUnavailableError):
        await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert await WafCooldownRepository(redis).is_active() is False


async def test_active_cooldown_on_a_miss_fails_without_calling_alcampo(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)
    await WafCooldownRepository(redis).activate(base_seconds=180, max_seconds=900)

    with pytest.raises(UpstreamUnavailableError):
        await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert scraper.calls == []


async def test_active_cooldown_still_serves_cache_hits(redis: fakeredis.FakeAsyncRedis) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)
    first = await service.search(ProductQuery(postal_code="28001", term="leche"))
    scraper.calls.clear()
    await WafCooldownRepository(redis).activate(base_seconds=180, max_seconds=900)

    second = await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert second == first
    assert scraper.calls == []


async def test_without_cooldown_a_miss_calls_alcampo(redis: fakeredis.FakeAsyncRedis) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)

    await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert scraper.calls == ["leche"]


async def test_active_cooldown_raises_cooldown_active_error(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    service, _ = make_service(FakeScraper(make_raw_response(has_products=True)), redis)
    await WafCooldownRepository(redis).activate(base_seconds=180, max_seconds=900)

    with pytest.raises(CooldownActiveError):
        await service.search(ProductQuery(postal_code="28001", term="leche"))


def service_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "app.services.product_service"]


async def test_waf_block_logs_an_error_with_the_cooldown(
    redis: fakeredis.FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    service, _ = make_service(FailingScraper(UpstreamBlockedError("WAF challenge")), redis)

    with pytest.raises(UpstreamBlockedError):
        await service.search(ProductQuery(postal_code="28001", term="leche"))

    [record] = service_records(caplog)
    assert record.levelno == logging.ERROR
    assert "blocked" in record.getMessage()
    assert "cooldown_s=180" in record.getMessage()


async def test_waf_block_with_cooldown_disabled_says_so(
    redis: fakeredis.FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    service, _ = make_service(
        FailingScraper(UpstreamBlockedError("WAF challenge")), redis, waf_cooldown_seconds=0
    )

    with pytest.raises(UpstreamBlockedError):
        await service.search(ProductQuery(postal_code="28001", term="leche"))

    [record] = service_records(caplog)
    assert record.levelno == logging.ERROR
    assert "cooldown disabled" in record.getMessage()


async def test_plain_upstream_error_is_not_logged_by_the_service(
    redis: fakeredis.FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    service, _ = make_service(FailingScraper(UpstreamUnavailableError("503")), redis)

    with pytest.raises(UpstreamUnavailableError):
        await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert service_records(caplog) == []


async def test_recent_second_waf_block_logs_the_doubled_cooldown(
    redis: fakeredis.FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    # spec 008 RF-8, RF-9: the ERROR states the duration actually applied.
    service, _ = make_service(FailingScraper(UpstreamBlockedError("WAF challenge")), redis)
    for _ in range(2):
        await redis.delete(WAF_COOLDOWN_KEY)  # let the second search reach Alcampo
        with pytest.raises(UpstreamBlockedError):
            await service.search(ProductQuery(postal_code="28001", term="leche"))

    first, second = service_records(caplog)
    assert "cooldown_s=180" in first.getMessage()
    assert "cooldown_s=360" in second.getMessage()
    assert 359 <= await redis.ttl(WAF_COOLDOWN_KEY) <= 360


# --- spec 008 RF-2: normalized term -------------------------------------------


async def test_other_case_hits_the_cache_and_returns_the_normalized_term(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)
    await service.search(ProductQuery(postal_code="28001", term="leche"))
    scraper.calls.clear()

    response = await service.search(ProductQuery(postal_code="28001", term="Leche"))

    assert scraper.calls == []
    # The term actually searched, as Mercadona does (spec 009 RF-10, spec-D5).
    assert response.search.term == "leche"


async def test_miss_sends_and_returns_the_normalized_term(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)

    response = await service.search(ProductQuery(postal_code="28001", term="LECHE   entera"))

    assert scraper.calls == ["leche entera"]
    assert response.search.term == "leche entera"


# --- spec 008 RF-7: search timeout ---------------------------------------------


async def test_search_that_exceeds_the_timeout_fails_and_is_cancelled(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scraper = HangingScraper()
    service, cache = make_service(scraper, redis, search_timeout_seconds=0.05)

    # Safety net so a missing timeout fails the test instead of hanging it.
    async with asyncio.timeout(2):
        with pytest.raises(UpstreamUnavailableError) as exc_info:
            await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert exc_info.value.reason == "search timeout"
    assert not isinstance(exc_info.value, UpstreamBlockedError)
    assert scraper.cancelled is True
    assert await cache.get(warehouse="5", term="leche", page=1, page_size=50) is None


# --- spec 008 RF-1, RF-11: simultaneous identical searches and origin log -----


async def settle() -> None:
    """Let every pending search reach its first real wait (no wall-clock sleep)."""
    for _ in range(50):
        await asyncio.sleep(0)


def origins(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        r.getMessage().removeprefix("search served source=")
        for r in service_records(caplog)
        if r.getMessage().startswith("search served")
    ]


async def test_simultaneous_identical_misses_call_alcampo_once(
    redis: fakeredis.FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    scraper = GatedScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)
    query = ProductQuery(postal_code="28001", term="leche")
    tasks = [asyncio.create_task(service.search(query)) for _ in range(10)]
    await settle()

    scraper.gate.set()
    responses = await asyncio.gather(*tasks)

    assert scraper.calls == ["leche"]
    assert all(response == responses[0] for response in responses)
    assert sorted(origins(caplog)) == ["miss"] + ["shared"] * 9


async def test_spelling_variants_share_the_request_and_the_normalized_term(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scraper = GatedScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)
    lower = asyncio.create_task(service.search(ProductQuery(postal_code="28001", term="leche")))
    upper = asyncio.create_task(service.search(ProductQuery(postal_code="08001", term="Leche")))
    await settle()

    scraper.gate.set()

    assert scraper.calls == ["leche"]
    assert (await lower).search.term == "leche"
    assert (await upper).search.term == "leche"
    assert (await upper).search.postal_code == "08001"


async def test_a_shared_waf_challenge_starts_a_single_cooldown(
    redis: fakeredis.FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    scraper = GatedScraper(
        make_raw_response(has_products=True), error=UpstreamBlockedError("WAF challenge")
    )
    service, _ = make_service(scraper, redis)
    query = ProductQuery(postal_code="28001", term="leche")
    tasks = [asyncio.create_task(service.search(query)) for _ in range(3)]
    await settle()

    scraper.gate.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert all(isinstance(r, UpstreamBlockedError) for r in results)
    assert len(scraper.calls) == 1
    blocked = [r for r in service_records(caplog) if "blocked" in r.getMessage()]
    assert len(blocked) == 1
    assert await redis.get("waf:cooldown:last") == b"180"  # not doubled by the waiters


async def test_a_cache_hit_is_logged_as_hit(
    redis: fakeredis.FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    service, _ = make_service(FakeScraper(make_raw_response(has_products=True)), redis)
    query = ProductQuery(postal_code="28001", term="leche")

    await service.search(query)
    await service.search(query)

    assert origins(caplog) == ["miss", "hit"]


# --- spec 007: search in the real region ---------------------------------------


class RecordingScraper(FakeScraper):
    def __init__(self, response: AlcampoSearchResponse) -> None:
        super().__init__(response)
        self.clients: list[object] = []

    async def search(
        self,
        term: str,
        *,
        client: object = None,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> AlcampoSearchResponse:
        self.clients.append(client)
        return await super().search(term, client=client)


async def test_the_search_goes_out_with_the_region_session(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scraper = RecordingScraper(make_raw_response(has_products=True))
    sessions = FakeSessions()
    service, _ = make_service(
        scraper, redis, regions=FakeRegions({"35001": TELDE}), sessions=sessions
    )

    response = await service.search(ProductQuery(postal_code="35001", term="leche"))

    assert scraper.clients == ["client-of-32"]
    assert sessions.asked == ["32"]
    assert response.search.warehouse == "32"
    assert await redis.exists("search:32:leche:1:50") == 1


async def test_two_regions_never_share_a_cache_entry(redis: fakeredis.FakeAsyncRedis) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis, regions=FakeRegions({"35001": TELDE}))

    madrid = await service.search(ProductQuery(postal_code="28001", term="leche"))
    canarias = await service.search(ProductQuery(postal_code="35001", term="leche"))

    assert scraper.calls == ["leche", "leche"]  # the second one is not a cache hit
    assert (madrid.search.warehouse, canarias.search.warehouse) == ("5", "32")


async def test_a_cache_hit_needs_no_session(redis: fakeredis.FakeAsyncRedis) -> None:
    sessions = FakeSessions()
    service, _ = make_service(
        FakeScraper(make_raw_response(has_products=True)), redis, sessions=sessions
    )
    await service.search(ProductQuery(postal_code="28001", term="leche"))

    await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert sessions.asked == ["5"]  # only the miss


async def test_a_postal_code_not_served_propagates(redis: fakeredis.FakeAsyncRedis) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    regions = FakeRegions({"51001": PostalCodeNotServedError("51001")})
    service, _ = make_service(scraper, redis, regions=regions)

    with pytest.raises(PostalCodeNotServedError):
        await service.search(ProductQuery(postal_code="51001", term="agua"))

    assert scraper.calls == []


async def test_a_waf_challenge_while_resolving_the_region_starts_the_cooldown(
    redis: fakeredis.FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    regions = FakeRegions({"35001": UpstreamBlockedError("WAF challenge")})
    service, _ = make_service(
        FakeScraper(make_raw_response(has_products=True)), redis, regions=regions
    )

    with pytest.raises(UpstreamBlockedError):
        await service.search(ProductQuery(postal_code="35001", term="agua"))

    assert await WafCooldownRepository(redis).is_active() is True
    assert any("blocked" in r.getMessage() for r in service_records(caplog))


# --- spec 010 RF-6: the traffic breakdown of every challenge -------------------


async def test_a_waf_challenge_logs_the_recent_outbound_traffic(
    redis: fakeredis.FakeAsyncRedis, caplog: pytest.LogCaptureFixture
) -> None:
    traffic = TrafficLog()
    for kind in ("search", "search", "search", "resolution", "resolution"):
        traffic.record(kind)
    traffic.record_status(400)
    service, _ = make_service(
        FailingScraper(UpstreamBlockedError("WAF challenge")), redis, traffic=traffic
    )

    with pytest.raises(UpstreamBlockedError):
        await service.search(ProductQuery(postal_code="28001", term="leche"))

    [error] = [r for r in service_records(caplog) if "blocked" in r.getMessage()]
    message = error.getMessage()
    assert "cooldown_s=180" in message
    assert "recent_traffic=1m[search=3 resolution=2 session=0]" in message
    assert "4xx_15m=1" in message


# --- spec 009 RF-3: Alcampo receives at most 50 characters ----------------------


async def test_a_long_term_is_cut_to_50_characters_for_alcampo(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    # As Alcampo's own web client does (Fase 0 §1); no trailing space after the cut.
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)
    term = "a" * 49 + " " + "b" * 30  # 80 characters, the 50th is a space

    await service.search(ProductQuery(postal_code="28001", term=term))

    assert scraper.calls == ["a" * 49]


# --- spec 009 RF-4, RF-5: totals of the first page ------------------------------


async def test_the_first_page_reports_the_estimated_total_and_pages(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    # More pages behind (a token): the total is the categories' estimate (plan-D4).
    raw = AlcampoSearchResponse.model_validate(
        {
            "productGroups": [{"decoratedProducts": [RAW_PRODUCT]}],
            "metadata": {"nextPageToken": "tok-2"},
            "additionalPageInfo": {"categories": [{"productCount": 535}, {"productCount": 134}]},
        }
    )
    service, _ = make_service(FakeScraper(raw), redis)

    result = await service.search(ProductQuery(postal_code="28001", term="leche"))

    assert result.search.total_results == 669
    assert result.search.total_pages == 14  # ceil(669 / 50)
    assert result.search.page == 1


# --- spec 009 RF-6, RF-9: pages, their cache entries and the end ---------------


async def test_a_page_walks_the_cursor_and_caches_every_page_paid_for(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scraper = ChainedScraper(pages=5)
    service, _ = make_service(scraper, redis)  # type: ignore[arg-type]

    result = await service.search(ProductQuery(postal_code="28001", term="leche", page=2))

    assert [p.id for p in result.products] == ["p2"]
    assert result.search.page == 2
    assert scraper.calls == [None, "tok-2"]
    assert sorted(await redis.keys("search:*")) == [b"search:5:leche:1:50", b"search:5:leche:2:50"]


async def test_the_page_size_reaches_alcampo_and_the_response(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scraper = ChainedScraper(pages=5)
    service, _ = make_service(scraper, redis)  # type: ignore[arg-type]

    result = await service.search(ProductQuery(postal_code="28001", term="leche", page_size=10))

    assert scraper.page_sizes == [10]
    assert result.search.page_size == 10
    assert await redis.keys("search:*") == [b"search:5:leche:1:10"]


async def test_a_page_past_the_last_one_is_not_cached(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scraper = ChainedScraper(pages=2)
    service, _ = make_service(scraper, redis)  # type: ignore[arg-type]

    with pytest.raises(PageOutOfRangeError):
        await service.search(ProductQuery(postal_code="28001", term="leche", page=4))

    assert sorted(await redis.keys("search:*")) == [b"search:5:leche:1:50", b"search:5:leche:2:50"]


# --- spec 009 RF-10: search.term is the term actually searched ------------------


async def test_a_long_term_is_returned_as_cut_for_alcampo(
    redis: fakeredis.FakeAsyncRedis,
) -> None:
    scraper = FakeScraper(make_raw_response(has_products=True))
    service, _ = make_service(scraper, redis)

    response = await service.search(
        ProductQuery(postal_code="28001", term="A" * 49 + " " + "b" * 30)
    )

    assert response.search.term == "a" * 49
