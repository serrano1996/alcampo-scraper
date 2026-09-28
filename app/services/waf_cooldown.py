"""Redis marker that pauses outbound searches after an AWS WAF challenge.

Alcampo's WAF blocks the whole egress IP for 2-4 minutes (Fase 0 §5), so the
marker is a single global key, not one per term or region (spec 002 RNF-4).
Every instance behind the same IP shares it through Redis.

The cooldown grows when challenges repeat (spec 008 RF-8, plan-D7): a fixed
180 s can fall short of a 4-minute block, and hitting Alcampo right after it
renews the block. A second key remembers the last duration for
`max_seconds`, which is what "recent" means.
"""

from redis.asyncio import Redis

WAF_COOLDOWN_KEY = "waf:cooldown"
WAF_COOLDOWN_LAST_KEY = "waf:cooldown:last"


class WafCooldownRepository:
    """Read and set the WAF cooldown marker (spec 002 RF-15, RF-18, RF-19; spec 008 RF-8)."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def is_active(self) -> bool:
        """Return `True` while the marker exists; Redis expiry ends the cooldown."""
        return bool(await self._redis.exists(WAF_COOLDOWN_KEY))

    async def activate(self, *, base_seconds: int, max_seconds: int) -> int:
        """Start a cooldown and return its duration in seconds (0 = disabled).

        The first challenge gets `base_seconds`; one within `max_seconds` of the
        previous gets double the previous duration, capped at `max_seconds`
        (180 -> 360 -> 720 -> 900 with the defaults). Always overwritten, never
        `NX`: a new challenge re-confirms the block (spec 002 plan-D5).
        """
        if base_seconds <= 0:
            return 0
        last = await self._redis.get(WAF_COOLDOWN_LAST_KEY)
        duration = base_seconds if last is None else min(int(last) * 2, max_seconds)
        await self._redis.set(WAF_COOLDOWN_KEY, "1", ex=duration)
        await self._redis.set(WAF_COOLDOWN_LAST_KEY, str(duration), ex=max_seconds)
        return duration
