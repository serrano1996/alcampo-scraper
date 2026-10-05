"""Pagination over Alcampo's cursor-based search (spec 009)."""

from collections.abc import Awaitable, Callable
from math import ceil
from typing import NamedTuple, Protocol

import httpx

from app.exceptions import PageOutOfRangeError, UpstreamUnavailableError
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


def next_page_token(raw: AlcampoSearchResponse) -> str | None:
    return raw.metadata.next_page_token if raw.metadata is not None else None


def is_last_page(raw: AlcampoSearchResponse) -> bool:
    return next_page_token(raw) is None


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


# --- Walking the cursor (spec 009 RF-6, RF-7, plan-D1, plan-D2) ----------------

# (sent term, page size, page) -> the token that fetches that page.
CursorKey = tuple[str, int, int]


class SearchScraper(Protocol):
    async def search(
        self,
        term: str,
        *,
        client: httpx.AsyncClient,
        page_size: int = ...,
        page_token: str | None = ...,
    ) -> AlcampoSearchResponse: ...


class CursorSession(Protocol):
    """A region session: its cookies and the page tokens Alcampo gave to it."""

    @property
    def client(self) -> httpx.AsyncClient: ...

    @property
    def cursors(self) -> dict[CursorKey, str]: ...


Bound = Callable[[Awaitable[AlcampoSearchResponse]], Awaitable[AlcampoSearchResponse]]
OnPassed = Callable[[int, AlcampoSearchResponse], Awaitable[None]]


class PageWalker:
    """Fetches page N from the deepest page whose token the session knows (plan-D2).

    Each request goes through `bound` on its own (its own time budget) and, inside
    the scraper, through the outbound gate (spec 010): a cold deep page can be cut
    by the short window, but the pages already paid for are kept (plan-D3).
    """

    def __init__(self, scraper: SearchScraper, *, bound: Bound) -> None:
        self._scraper = scraper
        self._bound = bound

    async def walk(
        self,
        session: CursorSession,
        term: str,
        *,
        page: int,
        page_size: int,
        on_passed: OnPassed,
    ) -> AlcampoSearchResponse:
        """Return page `page`; `on_passed` gets every earlier page fetched on the way.

        Raises `PageOutOfRangeError` if the search ends before `page`.
        """
        cursors = session.cursors
        number = next(k for k in range(page, 0, -1) if k == 1 or (term, page_size, k) in cursors)
        while True:
            key = (term, page_size, number)
            token = cursors.get(key) if number > 1 else None
            try:
                raw = await self._bound(
                    self._scraper.search(
                        term, client=session.client, page_size=page_size, page_token=token
                    )
                )
            except UpstreamUnavailableError as exc:
                if token is not None and exc.status_code is not None:
                    # Rejected despite plan-D1 (R3): never reuse it, walk again next time.
                    del cursors[key]
                raise
            following = next_page_token(raw)
            if following is not None:
                cursors[(term, page_size, number + 1)] = following
            if number == page:
                return raw
            # Already paid for: cached by the caller (RF-9), even if it is the end.
            await on_passed(number, raw)
            if following is None:
                raise PageOutOfRangeError(page)
            number += 1
