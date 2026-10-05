"""Spec 009: totals (RF-4, RF-5, plan-D4) and walking Alcampo's cursor (RF-6, RF-7, plan-D2)."""

from collections.abc import Awaitable

import pytest

from app.exceptions import PageOutOfRangeError, UpstreamUnavailableError
from app.models.alcampo import AlcampoSearchResponse
from app.models.product import MAX_PAGE
from app.services.pagination import (
    CursorKey,
    PageTotals,
    PageWalker,
    estimate_total,
    page_totals,
)
from tests.services.pagination_doubles import ChainedScraper


def raw(
    *, counts: list[int | None] | None = None, token: str | None = "tok"
) -> AlcampoSearchResponse:
    """A page with top-level category counts and, unless last, a next-page token."""
    body: dict[str, object] = {
        "productGroups": [],
        "metadata": {"nextPageToken": token} if token else {},
    }
    if counts is not None:
        body["additionalPageInfo"] = {
            "categories": [
                # Child counts are already inside their parent's: never added.
                {"productCount": c, "childCategories": [{"productCount": 999}]}
                for c in counts
            ]
        }
    return AlcampoSearchResponse.model_validate(body)


def test_the_estimate_adds_up_the_top_level_categories() -> None:
    assert estimate_total(raw(counts=[535, 100, 34])) == 669


def test_without_categories_the_estimate_is_zero() -> None:
    assert estimate_total(raw()) == 0
    assert estimate_total(raw(counts=[])) == 0
    assert estimate_total(raw(counts=[None, 7])) == 7


def test_a_middle_page_uses_the_estimate() -> None:
    totals = page_totals(raw(counts=[669]), page=2, page_size=50, on_page=50)

    assert totals == PageTotals(total_results=669, total_pages=14)  # ceil(669 / 50)


def test_total_pages_never_go_past_max_page() -> None:
    totals = page_totals(raw(counts=[5000]), page=1, page_size=50, on_page=50)

    assert totals == PageTotals(total_results=5000, total_pages=MAX_PAGE)


@pytest.mark.parametrize(
    ("page", "on_page", "expected"),
    [
        (1, 12, PageTotals(total_results=12, total_pages=1)),
        (3, 20, PageTotals(total_results=120, total_pages=3)),  # (3 - 1) * 50 + 20
    ],
)
def test_the_last_page_makes_the_total_exact(page: int, on_page: int, expected: PageTotals) -> None:
    # The estimate is ignored: on the last page the real count is known (spec-D3).
    totals = page_totals(raw(counts=[669], token=None), page=page, page_size=50, on_page=on_page)

    assert totals == expected


def test_an_empty_first_page_has_no_results_and_no_pages() -> None:
    totals = page_totals(raw(token=None), page=1, page_size=50, on_page=0)

    assert totals == PageTotals(total_results=0, total_pages=0)


def test_an_estimate_below_what_was_already_seen_is_raised() -> None:
    # 669 vs 670 live (spec §10): the estimate can fall short. Never report fewer
    # results, or fewer pages, than the ones already served.
    totals = page_totals(raw(counts=[3]), page=2, page_size=50, on_page=50)

    assert totals == PageTotals(total_results=100, total_pages=2)


# --- spec 009 RF-6, RF-7: walking the cursor (plan-D1, plan-D2) -----------------


class Session:
    def __init__(self) -> None:
        self.client = "client"
        self.cursors: dict[CursorKey, str] = {}


def page_of(raw: AlcampoSearchResponse) -> int:
    product = raw.product_groups[0].decorated_products[0]
    assert isinstance(product, dict)
    return int(str(product["retailerProductId"]).removeprefix("p"))


async def walk(
    scraper: ChainedScraper, session: Session, page: int
) -> tuple[AlcampoSearchResponse, list[int]]:
    passed: list[int] = []

    async def on_passed(number: int, raw: AlcampoSearchResponse) -> None:
        passed.append(number)

    async def bound(call: Awaitable[AlcampoSearchResponse]) -> AlcampoSearchResponse:
        return await call

    walker = PageWalker(scraper, bound=bound)
    raw = await walker.walk(session, "leche", page=page, page_size=50, on_passed=on_passed)
    return raw, passed


async def test_a_cold_page_walks_from_the_first_one() -> None:
    scraper, session = ChainedScraper(pages=5), Session()

    raw, passed = await walk(scraper, session, page=3)

    assert page_of(raw) == 3
    assert scraper.calls == [None, "tok-2", "tok-3"]
    assert passed == [1, 2]  # the pages paid for on the way, for the cache (RF-9)
    assert session.cursors == {
        ("leche", 50, 2): "tok-2",
        ("leche", 50, 3): "tok-3",
        ("leche", 50, 4): "tok-4",
    }


async def test_known_tokens_cost_a_single_request() -> None:
    scraper, session = ChainedScraper(pages=5), Session()
    await walk(scraper, session, page=2)
    scraper.calls.clear()

    raw, passed = await walk(scraper, session, page=3)

    assert page_of(raw) == 3
    assert scraper.calls == ["tok-3"]
    assert passed == []


async def test_tokens_are_per_term_and_page_size() -> None:
    scraper, session = ChainedScraper(pages=5), Session()
    session.cursors[("agua", 50, 3)] = "tok-3"
    session.cursors[("leche", 10, 3)] = "tok-3"

    await walk(scraper, session, page=3)

    assert scraper.calls == [None, "tok-2", "tok-3"]


async def test_a_page_past_the_last_one_is_out_of_range() -> None:
    scraper, session = ChainedScraper(pages=3), Session()

    passed: list[int] = []

    async def on_passed(number: int, raw: AlcampoSearchResponse) -> None:
        passed.append(number)

    async def bound(call: Awaitable[AlcampoSearchResponse]) -> AlcampoSearchResponse:
        return await call

    with pytest.raises(PageOutOfRangeError):
        await PageWalker(scraper, bound=bound).walk(
            session, "leche", page=5, page_size=50, on_passed=on_passed
        )

    assert scraper.calls == [None, "tok-2", "tok-3"]
    assert passed == [1, 2, 3]  # all paid for, so all cached; nothing for page 5


async def test_a_renewed_session_walks_again() -> None:
    # Tokens are bound to the session (spec §10): a new one starts empty (plan-D1).
    scraper = ChainedScraper(pages=5)
    await walk(scraper, Session(), page=3)
    scraper.calls.clear()

    await walk(scraper, Session(), page=3)

    assert scraper.calls == [None, "tok-2", "tok-3"]


async def test_a_rejected_token_is_forgotten() -> None:
    scraper, session = ChainedScraper(pages=5, reject={"tok-3"}), Session()
    session.cursors[("leche", 50, 3)] = "tok-3"

    with pytest.raises(UpstreamUnavailableError):
        await walk(scraper, session, page=3)

    assert ("leche", 50, 3) not in session.cursors
