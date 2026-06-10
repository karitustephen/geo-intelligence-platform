"""
Rate Limiting Middleware for Geospatial Intelligence API
Distributed rate limiting with Redis and local fallback
"""

import time
from collections import defaultdict, deque
from typing import Dict
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import get_config
from utils.response import error_response
from logging_config import get_logger
from .auth_middleware import resolve_client_ip

logger = get_logger(__name__)


class BoundedDeque(deque):
    """Deque with maximum size limit and TTL support"""

    def __init__(self, maxlen: int, ttl_seconds: int = 60):
        super().__init__(maxlen=maxlen)
        self.ttl_seconds = ttl_seconds
        self.timestamps = deque(maxlen=maxlen)

    def append(self, x):
        super().append(x)
        self.timestamps.append(time.time())

    def clean_expired(self, now: float) -> int:
        removed = 0
        while self.timestamps and now - self.timestamps[0] > self.ttl_seconds:
            self.popleft()
            self.timestamps.popleft()
            removed += 1
        return removed

    def __len__(self):
        return super().__len__()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting with Redis-backed distributed counters"""

    def __init__(self, app, redis_client=None):
        super().__init__(app)
        self.config = get_config()
        self.redis_client = redis_client
        self.rate_limit_store: Dict[str, BoundedDeque] = defaultdict(
            lambda: BoundedDeque(maxlen=self.config.rate_limit.per_minute, ttl_seconds=60)
        )

    async def dispatch(self, request: Request, call_next):
        if request.url.path in ["/health", "/healthz", "/ready", "/ping", "/metrics"]:
            return await call_next(request)

        user = getattr(request.state, "user", {})
        user_id = user.get("user_id") if isinstance(user, dict) else None

        if user_id:
            key = f"user:{user_id}"
            limit = self.config.rate_limit.per_minute
        else:
            key = f"anon:{resolve_client_ip(request)}"
            limit = self.config.rate_limit.anonymous_per_minute

        now = time.time()

        if self.redis_client:
            try:
                minute_key = f"rl:{key}:{int(now // 60)}"
                count = await self.redis_client.incr(minute_key)
                if count == 1:
                    await self.redis_client.expire(minute_key, 60)
                if count > limit:
                    logger.warning(f"Rate limit exceeded for {key}: {count}/{limit}")
                    return JSONResponse(
                        status_code=429,
                        content=error_response(
                            f"Rate limit exceeded. Limit: {limit} requests per minute",
                            429
                        ).model_dump()
                    )
            except Exception as e:
                logger.warning(f"Redis rate limit failed, falling back to local: {e}")

        window = self.rate_limit_store[key]
        window.clean_expired(now)

        if len(window) >= limit:
            logger.warning(f"Local rate limit exceeded for {key}: {len(window)}/{limit}")
            return JSONResponse(
                status_code=429,
                content=error_response(
                    "Rate limit exceeded. Please wait 60 seconds.",
                    429
                ).model_dump()
            )

        window.append(now)
        return await call_next(request)
