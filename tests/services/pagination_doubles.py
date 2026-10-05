"""Alcampo with pages chained by synthetic tokens (spec 009, tasks rule 4)."""

from app.exceptions import UpstreamUnavailableError
from app.models.alcampo import AlcampoSearchResponse


def raw_product(page: int) -> dict[str, object]:
    """One valid product per page, `p{page}`, so a page is recognizable once mapped."""
    return {
        "retailerProductId": f"p{page}",
        "name": f"Leche página {page}",
        "price": {"amount": "1.00"},
        "image": {"src": f"https://img.test/p{page}.jpg"},
        "categoryPath": ["Leche"],
    }


class ChainedScraper:
    """`pages` pages chained by `tok-2`, `tok-3`...; tokens in `reject` get a 400."""

    def __init__(self, pages: int, *, reject: set[str] | None = None) -> None:
        self.pages = pages
        self.reject = reject or set()
        self.calls: list[str | None] = []
        self.page_sizes: list[int] = []

    async def search(
        self,
        term: str,
        *,
        client: object = None,
        page_size: int = 50,
        page_token: str | None = None,
    ) -> AlcampoSearchResponse:
        self.calls.append(page_token)
        self.page_sizes.append(page_size)
        if page_token in self.reject:
            raise UpstreamUnavailableError("non-retryable status", status_code=400)
        page = 1 if page_token is None else int(page_token.removeprefix("tok-"))
        has_next = page < self.pages
        return AlcampoSearchResponse.model_validate(
            {
                "productGroups": [{"decoratedProducts": [raw_product(page)]}],
                "metadata": {"nextPageToken": f"tok-{page + 1}"} if has_next else {},
            }
        )
