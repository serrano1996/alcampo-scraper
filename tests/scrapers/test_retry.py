import httpx
import pytest

from app.exceptions import UpstreamUnavailableError
from app.scrapers.retry import send_with_retry


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
