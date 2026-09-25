from pathlib import Path

from app.core.config import Settings

ENV_EXAMPLE = Path(__file__).parents[2] / ".env.example"


def declared_variables() -> set[str]:
    names = set()
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            names.add(line.split("=", 1)[0].strip())
    return names


def test_env_example_documents_every_setting() -> None:
    expected = {name.upper() for name in Settings.model_fields}

    assert declared_variables() == expected
