"""Contract of the CI workflow (spec 006 RF-9, plan-D5).

The repository has no remote yet, so the workflow has never run on GitHub:
this test checks what it would run, and the same commands run locally.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")  # comes with uvicorn[standard]

WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"
REQUIRED_COMMANDS = [
    "ruff check .",
    "ruff format --check .",
    "mypy",
    "pytest -q",
    "docker build",
]


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def commands(workflow: dict) -> list[str]:
    return [step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"]]


def test_runs_on_every_push_and_pull_request(workflow: dict) -> None:
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads `on` as True
    assert {"push", "pull_request"} <= set(triggers)


def test_uses_the_minimum_supported_python(workflow: dict) -> None:
    versions = [
        step["with"]["python-version"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/setup-python")
    ]
    assert versions == ["3.11"]


@pytest.mark.parametrize("command", REQUIRED_COMMANDS)
def test_runs_every_check_that_closes_a_task(workflow: dict, command: str) -> None:
    assert any(command in run for run in commands(workflow))


def test_installs_the_dev_extra(workflow: dict) -> None:
    assert any('pip install -e ".[dev]"' in run for run in commands(workflow))
