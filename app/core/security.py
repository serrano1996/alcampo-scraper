"""API key authentication for `/api/v1` (spec 004)."""

import secrets
from typing import Annotated

from fastapi import HTTPException, Request, Security
from fastapi.security import APIKeyHeader

API_KEY_HEADER = "X-API-Key"
UNAUTHORIZED_DETAIL = "Invalid or missing API key"

# auto_error=False: we build the 401 ourselves so the body is identical for a
# missing and an invalid key and carries WWW-Authenticate (plan-D4, RF-3, RF-15).
api_key_header = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


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


def require_api_key(
    request: Request, api_key: Annotated[str | None, Security(api_key_header)]
) -> None:
    """Router-level dependency guarding every `/api/v1` endpoint (spec 004 RF-1..RF-3).

    Runs before query validation and before the service, so a rejected request
    never touches the cache, the WAF cooldown or Alcampo (verified, plan §2).
    Keys come from the settings stored by the lifespan (plan-D5).
    """
    if not is_valid_api_key(api_key, request.app.state.settings.api_keys):
        raise HTTPException(
            status_code=401,
            detail=UNAUTHORIZED_DETAIL,
            headers={"WWW-Authenticate": "ApiKey"},
        )
