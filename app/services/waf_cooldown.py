"""Redis marker that pauses outbound searches after an AWS WAF challenge.

Alcampo's WAF blocks the whole egress IP for 2-4 minutes (Fase 0 §5), so the
marker is a single global key, not one per term or region (spec 002 RNF-4).
Every instance behind the same IP shares it through Redis.
"""

from redis.asyncio import Redis

WAF_COOLDOWN_KEY = "waf:cooldown"


class WafCooldownRepository:
    """Read and set the WAF cooldown marker (spec 002 RF-15, RF-18, RF-19)."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def is_active(self) -> bool:
        """Return `True` while the marker exists; Redis expiry ends the cooldown."""
        return bool(await self._redis.exists(WAF_COOLDOWN_KEY))

    async def activate(self, *, ttl_seconds: int) -> None:
        """Start (or renew) the cooldown. `ttl_seconds == 0` disables it.

        Without `NX` on purpose (plan-D5): a new challenge re-confirms the block,
        so renewing the TTL is correct.
        """
        if ttl_seconds <= 0:
            return
        await self._redis.set(WAF_COOLDOWN_KEY, "1", ex=ttl_seconds)
