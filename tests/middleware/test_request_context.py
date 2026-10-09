import pytest

from app.middleware.request_context import MAX_PARAMS_LOGGED, params_for_log, redact_params


def test_secret_named_params_are_redacted_case_insensitively() -> None:
    params = [
        ("api_key", "s"),
        ("Token", "t"),
        ("KEY", "k"),
        ("x-api-key", "x"),
        ("apikey", "a"),
        ("term", "token"),
        ("postal_code", "28001"),
    ]

    assert redact_params(params) == [
        ("api_key", "***"),
        ("Token", "***"),
        ("KEY", "***"),
        ("x-api-key", "***"),
        ("apikey", "***"),
        ("term", "token"),  # judged by name, never by value
        ("postal_code", "28001"),
    ]


def test_params_without_secrets_are_unchanged() -> None:
    assert redact_params([("term", "leche")]) == [("term", "leche")]


# --- Spec 015 RF-4: by substring of the normalised name (F4, plan-D3) ---


@pytest.mark.parametrize(
    "name",
    [
        "access_token",
        "api-key",
        "apiToken",
        "X-Api-Token",
        "password",
        "passwd",
        "client_secret",
        "Authorization",
        " auth ",
        "keyword",  # accepted false positive (spec-D2)
    ],
)
def test_a_name_containing_a_secret_marker_is_redacted(name: str) -> None:
    # Before: only five exact names; these went to the logs in clear.
    assert redact_params([(name, "s3cr3t")]) == [(name, "***")]


@pytest.mark.parametrize("name", ["postal_code", "term", "page", "page_size"])
def test_the_api_own_params_are_never_redacted(name: str) -> None:
    assert redact_params([(name, "v")]) == [(name, "v")]


# --- Spec 015 RF-5: every value, and a cap (F5, plan-D3) ---


def test_a_repeated_param_keeps_every_value_in_order() -> None:
    # Before: a dict kept only the last one.
    assert params_for_log(b"term=a&term=b&token=x") == repr(
        [("term", "a"), ("term", "b"), ("token", "***")]
    )


def test_the_logged_params_are_capped() -> None:
    logged = params_for_log(b"term=" + b"x" * 2000)

    assert logged.startswith("[('term', 'xxx")
    assert logged.endswith("...(truncated, 2014 chars)")
    assert len(logged) < MAX_PARAMS_LOGGED + 40


def test_short_params_are_logged_whole() -> None:
    assert params_for_log(b"postal_code=28001&term=leche") == repr(
        [("postal_code", "28001"), ("term", "leche")]
    )


# --- Review T11: the normalisation RF-4 asks for (W1) ---


@pytest.mark.parametrize("name", ["to.ken", "pa_ss", "a pi key", "\uff2b\uff25\uff39", "K.E-Y"])
def test_separators_and_unicode_variants_do_not_hide_a_marker(name: str) -> None:
    # Before: only "-" was normalised, and fullwidth letters were not folded.
    assert redact_params([(name, "s3cr3t")]) == [(name, "***")]
