from pathlib import Path

DOCKERIGNORE = Path(__file__).parents[2] / ".dockerignore"

# Allowlist (spec 005 RF-5, RNF-3, plan-D5): anything not readmitted stays out of
# the build context, so a new secret file (another .env, a credentials.json...)
# can never reach the image by default.
READMITTED = {"pyproject.toml", "app/"}
EXCLUDED_AGAIN = ["**/__pycache__/", "**/*.py[cod]"]


def rules() -> list[str]:
    lines = (line.strip() for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def test_everything_is_excluded_first() -> None:
    assert rules()[0] == "*"


def test_only_the_package_sources_are_readmitted() -> None:
    assert {rule[1:] for rule in rules() if rule.startswith("!")} == READMITTED


def test_bytecode_inside_app_is_excluded_again() -> None:
    current = rules()
    for pattern in EXCLUDED_AGAIN:
        assert pattern in current
        assert current.index(pattern) > current.index("!app/")
