import logging
from datetime import UTC, datetime

import httpx
import pytest

from app.exceptions import UpstreamBlockedError, UpstreamUnavailableError
from app.scrapers.retry import parse_retry_after, send_with_retry

NOW = datetime(2026, 9, 24, 10, 0, 0, tzinfo=UTC)


class FakeSleep:
    """Records requested delays instead of actually sleeping."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.calls.append(delay)


def make_response(status_code: int, *, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status_code, headers=headers or {}, request=httpx.Request("GET", "https://alcampo.test/")
    )


def sequence(*results: httpx.Response | Exception):
    """Build an async `send` that returns/raises each result in order, once."""
    remaining = list(results)

    async def send() -> httpx.Response:
        result = remaining.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    return send


async def test_success_on_first_attempt_does_not_sleep() -> None:
    sleep = FakeSleep()

    response = await send_with_retry(
        sequence(make_response(200)), max_attempts=3, base_delay=0.5, sleep=sleep
    )

    assert response.status_code == 200
    assert sleep.calls == []


async def test_retries_on_5xx_until_success() -> None:
    sleep = FakeSleep()

    response = await send_with_retry(
        sequence(make_response(503), make_response(503), make_response(200)),
        max_attempts=3,
        base_delay=0.5,
        sleep=sleep,
    )

    assert response.status_code == 200
    assert sleep.calls == [0.5, 1.0]


async def test_retries_on_429() -> None:
    sleep = FakeSleep()

    response = await send_with_retry(
        sequence(make_response(429), make_response(200)),
        max_attempts=3,
        base_delay=0.5,
        sleep=sleep,
    )

    assert response.status_code == 200
    assert sleep.calls == [0.5]


async def test_non_retryable_4xx_raises_after_a_single_call() -> None:
    sleep = FakeSleep()
    send = sequence(make_response(404), make_response(200))

    with pytest.raises(UpstreamUnavailableError):
        await send_with_retry(send, max_attempts=3, base_delay=0.5, sleep=sleep)

    assert sleep.calls == []


async def test_exhausted_5xx_retries_raise_upstream_unavailable() -> None:
    sleep = FakeSleep()
    send = sequence(make_response(503), make_response(503), make_response(503))

    with pytest.raises(UpstreamUnavailableError):
        await send_with_retry(send, max_attempts=3, base_delay=0.5, sleep=sleep)

    assert sleep.calls == [0.5, 1.0]


async def test_exhausted_transport_errors_raise_upstream_unavailable_not_httpx() -> None:
    sleep = FakeSleep()
    send = sequence(
        httpx.ConnectError("boom"), httpx.ConnectError("boom"), httpx.ConnectError("boom")
    )

    with pytest.raises(UpstreamUnavailableError):
        await send_with_retry(send, max_attempts=3, base_delay=0.5, sleep=sleep)


async def test_waf_challenge_raises_without_retry_even_on_202() -> None:
    sleep = FakeSleep()
    send = sequence(
        make_response(202, headers={"x-amzn-waf-action": "challenge"}), make_response(200)
    )

    with pytest.raises(UpstreamUnavailableError):
        await send_with_retry(send, max_attempts=3, base_delay=0.5, sleep=sleep)

    assert sleep.calls == []


async def test_waf_challenge_header_overrides_a_2xx_status() -> None:
    sleep = FakeSleep()
    send = sequence(make_response(200, headers={"x-amzn-waf-action": "challenge"}))

    with pytest.raises(UpstreamUnavailableError):
        await send_with_retry(send, max_attempts=3, base_delay=0.5, sleep=sleep)


async def test_retries_on_transport_error() -> None:
    sleep = FakeSleep()

    response = await send_with_retry(
        sequence(httpx.ConnectTimeout("boom"), make_response(200)),
        max_attempts=3,
        base_delay=0.5,
        sleep=sleep,
    )

    assert response.status_code == 200
    assert sleep.calls == [0.5]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("120", 120.0),
        ("0", 0.0),
        ("Wed, 24 Sep 2026 10:00:30 GMT", 30.0),
        ("Wed, 24 Sep 2026 09:59:00 GMT", 0.0),
    ],
)
def test_parse_retry_after_valid_values(value: str, expected: float) -> None:
    assert parse_retry_after(value, now=NOW) == expected


@pytest.mark.parametrize("value", [None, "", "abc", "-5", "1.5"])
def test_parse_retry_after_invalid_values_return_none(value: str | None) -> None:
    assert parse_retry_after(value, now=NOW) is None


class FakeUniform:
    """Fake `random.uniform` that returns a fixed value and records its bounds."""

    def __init__(self, value: float) -> None:
        self.value = value
        self.calls: list[tuple[float, float]] = []

    def __call__(self, low: float, high: float) -> float:
        self.calls.append((low, high))
        # Stay within bounds like the real `random.uniform` would.
        return min(max(self.value, low), high)


async def test_jitter_is_added_to_5xx_backoff() -> None:
    sleep = FakeSleep()
    uniform = FakeUniform(0.2)

    await send_with_retry(
        sequence(make_response(503), make_response(200)),
        max_attempts=3,
        base_delay=0.5,
        jitter_max=0.3,
        sleep=sleep,
        uniform=uniform,
    )

    assert sleep.calls == [0.7]
    assert uniform.calls == [(0, 0.3)]


async def test_jitter_is_added_to_transport_error_backoff() -> None:
    sleep = FakeSleep()

    await send_with_retry(
        sequence(httpx.ConnectTimeout("boom"), make_response(200)),
        max_attempts=3,
        base_delay=0.5,
        jitter_max=0.3,
        sleep=sleep,
        uniform=FakeUniform(0.2),
    )

    assert sleep.calls == [0.7]


async def test_without_jitter_max_waits_are_exact() -> None:
    sleep = FakeSleep()
    uniform = FakeUniform(0.2)

    await send_with_retry(
        sequence(make_response(503), make_response(503), make_response(200)),
        max_attempts=3,
        base_delay=0.5,
        sleep=sleep,
        uniform=uniform,
    )

    assert sleep.calls == [0.5, 1.0]
    assert all(high == 0.0 for _, high in uniform.calls)


async def retry_waits(*responses: httpx.Response) -> list[float]:
    """Run `send_with_retry` with jitter fixed at 0.3 and `now` fixed; return the waits."""
    sleep = FakeSleep()
    await send_with_retry(
        sequence(*responses),
        max_attempts=3,
        base_delay=0.5,
        jitter_max=0.3,
        sleep=sleep,
        uniform=FakeUniform(0.3),
        now=lambda: NOW,
    )
    return sleep.calls


def too_many_requests(retry_after: str | None = None) -> httpx.Response:
    headers = {"Retry-After": retry_after} if retry_after is not None else None
    return make_response(429, headers=headers)


async def test_429_waits_for_retry_after_seconds_plus_jitter() -> None:
    waits = await retry_waits(too_many_requests("2"), make_response(200))

    assert waits == [pytest.approx(2.3)]


async def test_429_retry_after_is_capped_at_60_seconds() -> None:
    waits = await retry_waits(too_many_requests("3600"), make_response(200))

    assert waits == [60.0]


async def test_429_cap_includes_the_jitter() -> None:
    waits = await retry_waits(too_many_requests("60"), make_response(200))

    assert waits == [60.0]


async def test_429_retry_after_http_date() -> None:
    waits = await retry_waits(
        too_many_requests("Wed, 24 Sep 2026 10:00:10 GMT"), make_response(200)
    )

    assert waits == [pytest.approx(10.3)]


async def test_429_without_retry_after_falls_back_to_backoff() -> None:
    waits = await retry_waits(too_many_requests(), make_response(200))

    assert waits == [pytest.approx(0.8)]


async def test_retry_after_on_503_is_ignored() -> None:
    waits = await retry_waits(make_response(503, headers={"Retry-After": "30"}), make_response(200))

    assert waits == [pytest.approx(0.8)]


async def test_persistent_429_raises_upstream_unavailable() -> None:
    sleep = FakeSleep()

    with pytest.raises(UpstreamUnavailableError):
        await send_with_retry(
            sequence(too_many_requests("1"), too_many_requests("1"), too_many_requests("1")),
            max_attempts=3,
            base_delay=0.5,
            sleep=sleep,
            now=lambda: NOW,
        )

    assert len(sleep.calls) == 2


async def test_waf_challenge_raises_upstream_blocked_error_after_one_call() -> None:
    sleep = FakeSleep()
    calls: list[int] = []

    async def send() -> httpx.Response:
        calls.append(1)
        return make_response(202, headers={"x-amzn-waf-action": "challenge"})

    with pytest.raises(UpstreamBlockedError):
        await send_with_retry(send, max_attempts=3, base_delay=0.5, jitter_max=0.3, sleep=sleep)

    assert len(calls) == 1
    assert sleep.calls == []


URL = "/search?q=leche"


def retry_records(caplog: pytest.LogCaptureFixture, level: int) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "app.scrapers.retry" and r.levelno == level]


async def test_each_retry_logs_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    await send_with_retry(
        sequence(make_response(503), make_response(200)),
        max_attempts=3,
        base_delay=0.5,
        sleep=FakeSleep(),
        url=URL,
    )

    [warning] = retry_records(caplog, logging.WARNING)
    message = warning.getMessage()
    assert "attempt=1" in message
    assert "503" in message
    assert repr(URL) in message
    assert "wait_s=0.50" in message
    assert retry_records(caplog, logging.ERROR) == []


async def test_transport_error_retry_names_the_error(caplog: pytest.LogCaptureFixture) -> None:
    await send_with_retry(
        sequence(httpx.ConnectTimeout("boom"), make_response(200)),
        max_attempts=3,
        base_delay=0.5,
        sleep=FakeSleep(),
        url=URL,
    )

    [warning] = retry_records(caplog, logging.WARNING)
    assert "ConnectTimeout" in warning.getMessage()


async def test_exhausted_retries_log_an_error(caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(UpstreamUnavailableError):
        await send_with_retry(
            sequence(make_response(503), make_response(503), make_response(503)),
            max_attempts=3,
            base_delay=0.5,
            sleep=FakeSleep(),
            url=URL,
        )

    assert len(retry_records(caplog, logging.WARNING)) == 2
    [error] = retry_records(caplog, logging.ERROR)
    assert "attempts=3" in error.getMessage()
    assert repr(URL) in error.getMessage()


async def test_exhausted_transport_errors_log_an_error(caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(UpstreamUnavailableError):
        await send_with_retry(
            sequence(httpx.ConnectError("x"), httpx.ConnectError("x")),
            max_attempts=2,
            base_delay=0.5,
            sleep=FakeSleep(),
            url=URL,
        )

    [error] = retry_records(caplog, logging.ERROR)
    assert "attempts=2" in error.getMessage()
    assert "ConnectError" in error.getMessage()


async def test_non_retryable_4xx_logs_an_error_without_warnings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with pytest.raises(UpstreamUnavailableError):
        await send_with_retry(
            sequence(make_response(404)), max_attempts=3, base_delay=0.5, sleep=FakeSleep(), url=URL
        )

    [error] = retry_records(caplog, logging.ERROR)
    assert "404" in error.getMessage()
    assert repr(URL) in error.getMessage()
    assert retry_records(caplog, logging.WARNING) == []


async def test_waf_challenge_is_not_logged_by_retry(caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(UpstreamBlockedError):
        await send_with_retry(
            sequence(make_response(202, headers={"x-amzn-waf-action": "challenge"})),
            max_attempts=3,
            base_delay=0.5,
            sleep=FakeSleep(),
            url=URL,
        )

    assert [r for r in caplog.records if r.name == "app.scrapers.retry"] == []


async def test_url_defaults_to_dash(caplog: pytest.LogCaptureFixture) -> None:
    await send_with_retry(
        sequence(make_response(503), make_response(200)),
        max_attempts=3,
        base_delay=0.5,
        sleep=FakeSleep(),
    )

    [warning] = retry_records(caplog, logging.WARNING)
    assert "url='-'" in warning.getMessage()


async def test_non_retryable_4xx_keeps_its_status_code() -> None:
    # spec 007 plan-D6: a 4xx on a stored destination means "forget the region".
    with pytest.raises(UpstreamUnavailableError) as exc_info:
        await send_with_retry(
            sequence(make_response(410)), max_attempts=3, base_delay=0, sleep=FakeSleep()
        )

    assert exc_info.value.status_code == 410


async def test_exhausted_5xx_has_no_status_code() -> None:
    with pytest.raises(UpstreamUnavailableError) as exc_info:
        await send_with_retry(
            sequence(make_response(503)), max_attempts=1, base_delay=0, sleep=FakeSleep()
        )

    assert exc_info.value.status_code is None


# --- Spec 015 RF-1: request errors that are not transport errors (F1, plan-D1) ---

REQUEST = httpx.Request("GET", "https://alcampo.test/")


def not_transport_errors() -> list[httpx.RequestError]:
    return [
        httpx.DecodingError("Error -3 while decompressing data", request=REQUEST),
        httpx.TooManyRedirects("Exceeded maximum allowed redirects.", request=REQUEST),
    ]


@pytest.mark.parametrize("error", not_transport_errors(), ids=["decoding", "redirects"])
async def test_a_request_error_is_retried(error: httpx.RequestError) -> None:
    sleep = FakeSleep()

    response = await send_with_retry(
        sequence(error, make_response(200)), max_attempts=3, base_delay=0.5, sleep=sleep
    )

    assert response.status_code == 200
    assert sleep.calls == [0.5]


@pytest.mark.parametrize("error", not_transport_errors(), ids=["decoding", "redirects"])
async def test_exhausted_request_errors_raise_upstream_unavailable(
    error: httpx.RequestError, caplog: pytest.LogCaptureFixture
) -> None:
    # Before: the httpx error escaped and the API answered 500.
    with pytest.raises(UpstreamUnavailableError):
        await send_with_retry(
            sequence(error, error, error), max_attempts=3, base_delay=0.5, sleep=FakeSleep()
        )

    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert errors == [f"retries exhausted attempts=3 reason={type(error).__name__} url='-'"]


# --- Spec 015 RF-2: a Retry-After that cannot become a finite wait (F2, plan-D2) ---

OVERFLOWING_DATE = "Wed, 21 Oct 99999999999999999999 07:28:00 GMT"


@pytest.mark.parametrize(
    "value", [OVERFLOWING_DATE, "9" * 400], ids=["overflowing-year", "infinite-seconds"]
)
def test_a_retry_after_that_is_not_a_finite_wait_is_ignored(value: str) -> None:
    # Before: the date raised OverflowError (a 500) and the digits became `inf`.
    assert parse_retry_after(value, now=NOW) is None


async def test_429_with_an_overflowing_retry_after_falls_back_to_backoff() -> None:
    waits = await retry_waits(too_many_requests(OVERFLOWING_DATE), make_response(200))

    assert waits == [pytest.approx(0.8)]


def test_a_huge_but_finite_retry_after_is_still_a_wait() -> None:
    # Capped to 60 s by the caller, as before (spec 015 §5).
    assert parse_retry_after("99999999999999999999", now=NOW) == 1e20
