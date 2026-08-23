import json
import logging
from typing import Any

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


class RedisJsonCache:
    """Small fail-open JSON cache used for non-authoritative iiko reads."""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def get(self, key: str) -> Any | None:
        try:
            value = await self.redis.get(key)
            if value is None:
                return None
            if isinstance(value, bytes):
                value = value.decode("utf-8")
            return json.loads(value)
        except (RedisError, UnicodeError, json.JSONDecodeError):
            logger.warning("Redis cache read failed key=%s", key, exc_info=True)
            return None

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        try:
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            await self.redis.set(key, payload, ex=ttl_seconds)
        except (RedisError, TypeError, ValueError):
            logger.warning("Redis cache write failed key=%s", key, exc_info=True)

    async def claim(self, key: str, ttl_seconds: int) -> bool:
        """Atomically claim a key; fail open when Redis itself is unavailable."""
        try:
            return bool(await self.redis.set(key, "1", ex=ttl_seconds, nx=True))
        except RedisError:
            logger.warning("Redis claim failed key=%s", key, exc_info=True)
            return True

    async def delete(self, *keys: str) -> None:
        if not keys:
            return
        try:
            await self.redis.delete(*keys)
        except RedisError:
            logger.warning("Redis cache delete failed", exc_info=True)

    async def ping(self) -> bool:
        try:
            return bool(await self.redis.ping())
        except RedisError:
            logger.warning("Redis ping failed", exc_info=True)
            return False

    async def close(self) -> None:
        await self.redis.aclose()
