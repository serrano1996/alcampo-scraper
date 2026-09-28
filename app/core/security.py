"""API key authentication for `/api/v1` (spec 004)."""

import secrets


def is_valid_api_key(candidate: str | None, valid_keys: frozenset[str]) -> bool:
    """Return whether `candidate` matches one of `valid_keys` (spec 004 RF-4, RF-5, plan-D3).

    - Exact match: no stripping or case folding, a key is an opaque secret.
    - Compared as UTF-8 bytes: `secrets.compare_digest` raises `TypeError` on
      non-ASCII `str`, which would turn a bad header into a 500.
    - Checked against every key without short-circuiting, so response time does
      not reveal which configured key matched.
    """
    if not candidate:
        return False
    candidate_bytes = candidate.encode("utf-8")
    matched = False
    for key in valid_keys:
        matched |= secrets.compare_digest(candidate_bytes, key.encode("utf-8"))
    return matched
