import importlib
import tomllib
from pathlib import Path

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


# --- spec 006 RF-10..RF-12: dependencies and warnings ------------------------

PYPROJECT = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text("utf-8"))


def declared_requirements() -> list[str]:
    project = PYPROJECT["project"]
    return [*project["dependencies"], *project["optional-dependencies"]["dev"]]


@pytest.mark.parametrize("requirement", declared_requirements())
def test_every_dependency_has_an_upper_bound(requirement: str) -> None:
    # A new major version must be a decision, not a surprise (spec 006 RF-10).
    assert "<" in requirement


def test_warnings_fail_the_suite() -> None:
    # A deprecation is caught the day it appears (spec 006 RF-11).
    assert PYPROJECT["tool"]["pytest"]["ini_options"]["filterwarnings"] == ["error"]


def test_the_test_client_uses_httpx2() -> None:
    # Starlette's test client asks for httpx2 and warns with plain httpx (RF-12).
    assert any(
        req.startswith("httpx2") for req in PYPROJECT["project"]["optional-dependencies"]["dev"]
    )
