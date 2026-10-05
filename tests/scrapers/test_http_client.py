from collections.abc import Sequence

import httpx
import respx

from app.core.config import Settings
from app.scrapers.http_client import USER_AGENTS, create_http_client

# Literal copy of spec 002 RF-2: the pool is part of the contract, not an implementation detail.
EXPECTED_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/27.0 Safari/605.1.15",
)


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        alcampo_base_url="https://alcampo.test",
        redis_url="redis://localhost:6379/0",
    )


class RecordingChoice:
    """Fake `random.choice` that records its calls and returns a fixed pool index."""

    def __init__(self, index: int) -> None:
        self.index = index
        self.calls: list[Sequence[str]] = []

    def __call__(self, pool: Sequence[str]) -> str:
        self.calls.append(pool)
        return pool[self.index]


async def test_create_http_client_sets_base_url_and_timeout() -> None:
    client = create_http_client(make_settings())
    try:
        assert client.base_url == httpx.URL("https://alcampo.test")
        assert client.timeout.connect == 10.0
    finally:
        await client.aclose()


async def test_create_http_client_sets_realistic_headers() -> None:
    client = create_http_client(make_settings(), choose=RecordingChoice(0))
    try:
        assert client.headers["Accept"] == "application/json"
        assert client.headers["Accept-Language"].startswith("es-ES")
    finally:
        await client.aclose()


def test_user_agent_pool_is_exactly_the_spec_pool() -> None:
    assert USER_AGENTS == EXPECTED_USER_AGENTS
    assert len(set(USER_AGENTS)) == len(USER_AGENTS)


async def test_user_agent_is_chosen_once_from_the_pool() -> None:
    choose = RecordingChoice(3)

    client = create_http_client(make_settings(), choose=choose)
    try:
        assert choose.calls == [USER_AGENTS]
        assert client.headers["User-Agent"] == USER_AGENTS[3]
    finally:
        await client.aclose()


async def test_default_user_agent_belongs_to_the_pool() -> None:
    client = create_http_client(make_settings())
    try:
        assert client.headers["User-Agent"] in USER_AGENTS
    finally:
        await client.aclose()


async def test_client_sends_browser_context_headers_independent_of_base_url() -> None:
    client = create_http_client(make_settings(), choose=RecordingChoice(0))
    try:
        assert client.headers["Referer"] == "https://www.compraonline.alcampo.es/"
        assert client.headers["ecom-request-source"] == "web"
    finally:
        await client.aclose()


@respx.mock
async def test_outgoing_request_has_referer_and_no_origin() -> None:
    route = respx.get("https://alcampo.test/ping").mock(return_value=httpx.Response(200))
    client = create_http_client(make_settings(), choose=RecordingChoice(0))
    try:
        await client.get("/ping")
    finally:
        await client.aclose()

    request = route.calls.last.request
    assert request.headers["Referer"] == "https://www.compraonline.alcampo.es/"
    assert "Origin" not in request.headers


async def test_the_http_timeout_comes_from_settings() -> None:
    # Spec 012 RF-4: connect, read, write and pool, all from HTTP_TIMEOUT_SECONDS.
    settings = Settings(
        _env_file=None,
        alcampo_base_url="https://alcampo.test",
        redis_url="redis://localhost:6379/0",
        http_timeout_seconds=3,
    )
    client = create_http_client(settings)
    try:
        timeout = client.timeout
        assert (timeout.connect, timeout.read, timeout.write, timeout.pool) == (3, 3, 3, 3)
    finally:
        await client.aclose()
