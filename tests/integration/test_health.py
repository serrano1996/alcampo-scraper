from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError

import app.main as main_module
from app.main import create_app


class DownRedis:
    """Redis that is unreachable: any command fails, only closing works."""

    async def aclose(self) -> None:
        pass

    def __getattr__(self, name: str) -> object:
        raise RedisConnectionError(f"redis is down ({name})")


def test_health_answers_without_key_redis_or_alcampo(integration_env, respx_mock) -> None:
    # The container HEALTHCHECK probes /health every 30 s: it must never touch
    # Redis or Alcampo's WAF, and must stay up when Redis is down (spec 005 RF-12).
    integration_env.setattr(main_module, "create_redis", lambda _url, **_: DownRedis())

    with TestClient(create_app()) as client:  # no X-API-Key; respx has no routes
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
