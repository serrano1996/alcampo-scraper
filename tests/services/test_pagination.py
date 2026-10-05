"""Spec 009 RF-4, RF-5: total results and pages from Alcampo's paging data (plan-D4)."""

import pytest

from app.models.alcampo import AlcampoSearchResponse
from app.models.product import MAX_PAGE
from app.services.pagination import PageTotals, estimate_total, page_totals


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
