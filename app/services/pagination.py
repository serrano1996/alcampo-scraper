"""Pagination over Alcampo's cursor-based search (spec 009)."""

from math import ceil
from typing import NamedTuple

from app.models.alcampo import AlcampoSearchResponse
from app.models.product import MAX_PAGE


class PageTotals(NamedTuple):
    total_results: int
    total_pages: int


def estimate_total(raw: AlcampoSearchResponse) -> int:
    """Sum of the top-level category counts: Alcampo gives no exact total (spec-D3).

    Child counts are already included in their parent's, so only the first level adds up.
    """
    if raw.additional_page_info is None:
        return 0
    return sum(c.product_count or 0 for c in raw.additional_page_info.categories)


def is_last_page(raw: AlcampoSearchResponse) -> bool:
    return raw.metadata is None or raw.metadata.next_page_token is None


def page_totals(
    raw: AlcampoSearchResponse, *, page: int, page_size: int, on_page: int
) -> PageTotals:
    """Total results and pages as seen from `page`, which holds `on_page` products (plan-D4).

    Exact on the last page; otherwise the estimate, never below what was already served.
    """
    seen = (page - 1) * page_size + on_page
    if is_last_page(raw):
        return PageTotals(total_results=seen, total_pages=page if seen else 0)
    total = max(estimate_total(raw), seen)
    return PageTotals(total_results=total, total_pages=min(ceil(total / page_size), MAX_PAGE))
