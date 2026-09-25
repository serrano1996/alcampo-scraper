import logging

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.mark.parametrize("level", ["ERROR", "DEBUG"])
def test_lifespan_configures_the_root_level_from_settings(
    integration_env: pytest.MonkeyPatch, level: str
) -> None:
    integration_env.setenv("LOG_LEVEL", level)

    with TestClient(create_app()):
        assert logging.getLogger().level == logging.getLevelName(level)
