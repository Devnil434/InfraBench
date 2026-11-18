"""Redis client and connection management."""

import time
import logging
from typing import Optional, Tuple

import redis.asyncio as aioredis
from redis.asyncio import Redis

from app.config import settings

logger = logging.getLogger(__name__)

_redis_client: Optional[Redis] = None


def get_redis_client() -> Redis:
    """Get or create singleton async Redis client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=3.0,
            socket_connect_timeout=3.0,
            health_check_interval=30,
        )
    return _redis_client


async def check_redis_health() -> Tuple[bool, float, Optional[str]]:
    """Check Redis cache connectivity and response latency.

    Returns:
        Tuple of (is_healthy, latency_ms, error_message)
    """
    start_time = time.perf_counter()
    try:
        client = get_redis_client()
        pong = await client.ping()
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        if pong:
            return True, latency_ms, None
        return False, latency_ms, "Redis did not respond with PONG"
    except Exception as exc:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        logger.warning(f"Redis health check failed: {exc}")
        return False, latency_ms, str(exc)


async def close_redis_client() -> None:
    """Close Redis client connection pool gracefully."""
    global _redis_client
    if _redis_client is not None:
        logger.info("Closing Redis connection pool...")
        await _redis_client.aclose()
        _redis_client = None
        logger.info("Redis connection pool closed.")
