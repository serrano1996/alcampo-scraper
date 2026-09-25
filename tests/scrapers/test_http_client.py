import httpx

from app.core.config import Settings
from app.scrapers.http_client import create_http_client


def make_settings() -> Settings:
    return Settings(
        _env_file=None,
        alcampo_base_url="https://alcampo.test",
        redis_url="redis://localhost:6379/0",
    )


async def test_create_http_client_sets_base_url_and_timeout() -> None:
    client = create_http_client(make_settings())
    try:
        assert client.base_url == httpx.URL("https://alcampo.test")
        assert client.timeout.connect == 10.0
    finally:
        await client.aclose()


async def test_create_http_client_sets_realistic_headers() -> None:
    client = create_http_client(make_settings())
    try:
        assert "Chrome/" in client.headers["User-Agent"]
        assert client.headers["Accept"] == "application/json"
        assert client.headers["Accept-Language"].startswith("es-ES")
    finally:
        await client.aclose()
