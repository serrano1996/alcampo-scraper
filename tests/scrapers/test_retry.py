import httpx

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
