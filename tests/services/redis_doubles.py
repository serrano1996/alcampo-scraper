"""Redis doubles shared by the degradation tests (spec 007 RF-15)."""

from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError


class BrokenRedis:
    """A Redis where every command fails with `error` (down, or hung past its timeout)."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def aclose(self) -> None:
        pass

    def __getattr__(self, name: str) -> object:
        raise self.error


DOWN = RedisConnectionError("Connection refused")
HUNG = RedisTimeoutError("Timeout reading from socket")


class CountingBrokenRedis(BrokenRedis):
    """A broken Redis that counts every command attempted (spec 007 RF-18)."""

    def __init__(self, error: Exception) -> None:
        super().__init__(error)
        self.attempts = 0

    def __getattr__(self, name: str) -> object:
        self.attempts += 1
        raise self.error
