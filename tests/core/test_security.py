import pytest

from app.core.security import is_valid_api_key

KEYS = frozenset({"k1", "k2"})


@pytest.mark.parametrize("candidate", ["k1", "k2"])
def test_any_configured_key_is_valid(candidate: str) -> None:
    assert is_valid_api_key(candidate, KEYS) is True


@pytest.mark.parametrize("candidate", ["k3", " k1", "k1 ", "K1", "", None])
def test_anything_else_is_invalid(candidate: str | None) -> None:
    assert is_valid_api_key(candidate, KEYS) is False


def test_non_ascii_candidate_is_rejected_without_error() -> None:
    # compare_digest raises TypeError on non-ASCII str: it must never become a 500.
    assert is_valid_api_key("clé", KEYS) is False


def test_non_ascii_configured_key_works() -> None:
    assert is_valid_api_key("clé", frozenset({"clé"})) is True


def test_no_configured_keys_rejects_everything() -> None:
    assert is_valid_api_key("k1", frozenset()) is False
