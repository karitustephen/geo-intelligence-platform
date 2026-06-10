"""
Redis service for caching and rate limiting
"""

import asyncio
import logging
from typing import Optional, Any
import redis.asyncio as aioredis

from config import get_config

logger = logging.getLogger(__name__)

redis_client: Optional[aioredis.Redis] = None
_health_task: Optional[asyncio.Task] = None


async def init_redis() -> Optional[aioredis.Redis]:
    """Initialize Redis connection"""
    global redis_client
    config = get_config()
    
    try:
        if config.redis.url:
            redis_url = config.redis.url
        else:
            redis_url = f"redis://{config.redis.host}:{config.redis.port}"
            if config.redis.password:
                redis_url = f"redis://:{config.redis.password}@{config.redis.host}:{config.redis.port}"
        
        client = aioredis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=config.redis.connect_timeout,
            socket_timeout=config.redis.socket_timeout,
            max_connections=config.redis.max_connections,
            retry_on_timeout=True,
            health_check_interval=30
        )
        
        await client.ping()
        redis_client = client
        logger.info("Redis connected successfully")
        
        global _health_task
        if _health_task is None:
            _health_task = asyncio.create_task(_redis_health_check())
        
        return client
        
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}. Running in memory-only mode.")
        redis_client = None
        return None


async def close_redis():
    """Close Redis connection"""
    global redis_client, _health_task
    if _health_task: _health_task.cancel()
    if redis_client:
        await redis_client.close()
        redis_client = None


async def _redis_health_check():
    """Background task to monitor Redis health"""
    global redis_client
    while True:
        await asyncio.sleep(30)
        try:
            if redis_client: await redis_client.ping()
        except Exception as e:
            logger.warning(f"Redis health check failed: {e}")
            redis_client = None


async def get_cache(key: str) -> Optional[str]:
    """Get value from cache"""
    if not redis_client: return None
    try:
        return await redis_client.get(key)
    except Exception:
        return None


async def set_cache(key: str, value: str, ttl: int = 3600) -> bool:
    """Set value in cache with TTL"""
    if not redis_client: return False
    try:
        await redis_client.setex(key, ttl, value)
        return True
    except Exception:
        return False