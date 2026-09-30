"""Spec 006 RF-1, RF-2: the lifespan's resources live in one typed object."""

from dataclasses import fields

import pytest
from fastapi.testclient import TestClient

from app.core.state import AppResources, resources
from app.main import create_app


def test_the_lifespan_stores_every_resource_in_one_typed_object(
    integration_env: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app()) as client:
        found = resources(client.app)

        assert isinstance(found, AppResources)
        assert all(getattr(found, f.name) is not None for f in fields(AppResources))
        assert found.settings.alcampo_base_url == "https://alcampo.test"


def test_resources_outside_the_lifespan_fail_clearly() -> None:
    with pytest.raises(RuntimeError, match="lifespan"):
        resources(create_app())
