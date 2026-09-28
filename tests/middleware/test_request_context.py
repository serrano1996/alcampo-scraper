from app.middleware.request_context import redact_params


def test_secret_named_params_are_redacted_case_insensitively() -> None:
    params = {
        "api_key": "s",
        "Token": "t",
        "KEY": "k",
        "x-api-key": "x",
        "apikey": "a",
        "term": "token",
        "postal_code": "28001",
    }

    assert redact_params(params) == {
        "api_key": "***",
        "Token": "***",
        "KEY": "***",
        "x-api-key": "***",
        "apikey": "***",
        "term": "token",  # judged by name, never by value
        "postal_code": "28001",
    }


def test_params_without_secrets_are_unchanged() -> None:
    assert redact_params({"term": "leche"}) == {"term": "leche"}
