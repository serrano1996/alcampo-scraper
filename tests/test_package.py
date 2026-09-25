import importlib

import pytest

PACKAGES = [
    "app",
    "app.api",
    "app.api.v1",
    "app.core",
    "app.models",
    "app.mappers",
    "app.scrapers",
    "app.services",
]


@pytest.mark.parametrize("name", PACKAGES)
def test_package_is_importable(name: str) -> None:
    assert importlib.import_module(name).__name__ == name
