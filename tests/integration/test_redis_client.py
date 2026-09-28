import fakeredis
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import create_app


def test_lifespan_builds_redis_with_the_configured_timeout(
    integration_env: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, float]] = []

    def spy(redis_url: str, *, timeout_seconds: float) -> fakeredis.FakeAsyncRedis:
        calls.append((redis_url, timeout_seconds))
        return fakeredis.FakeAsyncRedis()

    integration_env.setenv("REDIS_TIMEOUT_SECONDS", "0.5")
    integration_env.setattr(main_module, "create_redis", spy)

    with TestClient(create_app()):
        pass

    assert calls == [("redis://localhost:6379/0", 0.5)]
