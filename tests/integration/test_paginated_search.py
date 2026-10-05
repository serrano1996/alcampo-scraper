"""Spec 009 end to end: real app, fakeredis and respx with pages chained by tokens.

The pages are synthesized from the real `leche` response (tasks rule 4): its
products and category counts, with synthetic tokens `tok-2`, `tok-3`; the third
page is the last one (`metadata: {}`), as seen live (spec §10).
"""

import copy

import httpx
import respx
from fastapi.testclient import TestClient

from tests.integration.conftest import SEARCH_URL, load_fixture, mock_region_chain

PAGES = 3
SEARCH = {"postal_code": "28001", "term": "leche"}


def mock_paged_search(respx_mock: respx.MockRouter) -> respx.Route:
    mock_region_chain(respx_mock)
    real = load_fixture("alcampo_search_leche.json")

    def page(request: httpx.Request) -> httpx.Response:
        token = request.url.params.get("pageToken")
        number = 1 if token is None else int(token.removeprefix("tok-"))
        body = copy.deepcopy(real)
        body["metadata"] = {"nextPageToken": f"tok-{number + 1}"} if number < PAGES else {}
        return httpx.Response(200, json=body)

    return respx_mock.get(SEARCH_URL).mock(side_effect=page)


def tokens_sent(route: respx.Route) -> list[str | None]:
    return [call.request.url.params.get("pageToken") for call in route.calls]


def test_pages_in_order_cost_one_request_each(client: TestClient, respx_mock) -> None:
    route = mock_paged_search(respx_mock)

    pages = [
        client.get("/api/v1/products", params={**SEARCH, "page": n}).json()["search"]
        for n in (1, 2, 3)
    ]

    assert tokens_sent(route) == [None, "tok-2", "tok-3"]
    assert [p["page"] for p in pages] == [1, 2, 3]
    # Estimated from the categories (535), exact on the last page: 2 * 50 + 3.
    assert [p["total_results"] for p in pages] == [535, 535, 103]
    assert [p["total_pages"] for p in pages] == [11, 11, 3]


def test_a_repeated_page_comes_from_the_cache(client: TestClient, respx_mock) -> None:
    route = mock_paged_search(respx_mock)
    first = client.get("/api/v1/products", params={**SEARCH, "page": 3})

    again = client.get("/api/v1/products", params={**SEARCH, "page": 3})

    assert again.status_code == 200
    assert again.json() == first.json()
    assert tokens_sent(route) == [None, "tok-2", "tok-3"]  # the cold walk only


def test_a_page_past_the_last_one_is_404(client: TestClient, respx_mock) -> None:
    mock_paged_search(respx_mock)

    response = client.get("/api/v1/products", params={**SEARCH, "page": 5})

    assert response.status_code == 404
    assert response.json() == {"detail": "Page out of range"}
    assert response.headers["X-Request-ID"]


def test_without_parameters_the_first_page_of_50(client: TestClient, respx_mock) -> None:
    # Spec 009 RF-2: the usual response, plus the pagination fields.
    route = mock_paged_search(respx_mock)

    response = client.get("/api/v1/products", params={**SEARCH, "term": "LECHE"})

    assert response.status_code == 200
    search = response.json()["search"]
    assert search["term"] == "leche"
    assert (search["page"], search["page_size"]) == (1, 50)
    params = route.calls.last.request.url.params
    assert params["maxPageSize"] == "50"
    assert "pageToken" not in params
    assert len(response.json()["products"]) == 3
