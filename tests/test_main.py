from app.main import create_redis


def test_redis_client_has_connect_and_operation_timeouts() -> None:
    # Without them a hung Redis hangs every request (spec 007 RF-14). Building
    # the client does not connect, so no Redis is needed here.
    client = create_redis("redis://localhost:6379/0", timeout_seconds=2)

    kwargs = client.connection_pool.connection_kwargs
    assert kwargs["socket_connect_timeout"] == 2
    assert kwargs["socket_timeout"] == 2
