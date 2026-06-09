"""
Rate limiting middleware for the geospatial intelligence API
"""

import time
import asyncio
from collections import defaultdict, deque
from typing import Dict, Deque, Optional
from ipaddress import ip_address, ip_network

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from config import get_config
from utils.exceptions import RateLimitError
from utils.response import error_response


class RateLimitMiddleware(BaseHTTPMiddleware):
	"""Rate limiting middleware with Redis support"""
    
	def __init__(self, app, redis_client=None):
		super().__init__(app)
		self.config = get_config()
		self.redis_client = redis_client
		self.local_storage: Dict[str, Deque] = defaultdict(lambda: deque(maxlen=self.config.rate_limit.per_minute))
		self._cleanup_task = None
    
	async def dispatch(self, request: Request, call_next):
		# Get rate limit key
		key = await self._get_rate_limit_key(request)
        
		# Check rate limit
		if self.redis_client:
			allowed, remaining, reset = await self._check_redis_rate_limit(key)
		else:
			allowed, remaining, reset = self._check_local_rate_limit(key)
        
		if not allowed:
			return error_response(
				error="Rate limit exceeded",
				error_code="RATE_LIMIT_EXCEEDED",
				status_code=429,
				details={"limit": self.config.rate_limit.per_minute, "remaining": remaining, "reset_seconds": reset}
			)
        
		# Add rate limit headers
		response = await call_next(request)
		response.headers["X-RateLimit-Limit"] = str(self.config.rate_limit.per_minute)
		response.headers["X-RateLimit-Remaining"] = str(remaining)
		response.headers["X-RateLimit-Reset"] = str(reset)
        
		return response
    
	async def _get_rate_limit_key(self, request: Request) -> str:
		"""Get rate limit key for the request"""
		user = getattr(request.state, "user", {})
		user_id = user.get("user_id") if isinstance(user, dict) else None
        
		if user_id:
			return f"user:{user_id}"
        
		# Anonymous user - use IP address
		client_ip = self._get_client_ip(request)
		return f"ip:{client_ip}"
    
	def _get_client_ip(self, request: Request) -> str:
		"""Get client IP address with proxy support"""
		forwarded = request.headers.get("X-Forwarded-For")
		if forwarded:
			return forwarded.split(",")[0].strip()
		return request.client.host if request.client else "unknown"
    
	async def _check_redis_rate_limit(self, key: str) -> tuple:
		"""Check rate limit using Redis"""
		window_start = int(time.time() / 60) * 60
		redis_key = f"rate_limit:{key}:{window_start}"
        
		try:
			current = await self.redis_client.get(redis_key)
			count = int(current) if current else 0
            
			if count >= self.config.rate_limit.per_minute:
				remaining = 0
				reset = 60 - (int(time.time()) - window_start)
				return False, remaining, reset
            
			await self.redis_client.incr(redis_key)
			await self.redis_client.expire(redis_key, 60)
            
			remaining = self.config.rate_limit.per_minute - (count + 1)
			reset = 60 - (int(time.time()) - window_start)
			return True, remaining, reset
            
		except Exception as e:
			# Fallback to local rate limiting
			return self._check_local_rate_limit(key)
    
	def _check_local_rate_limit(self, key: str) -> tuple:
		"""Check rate limit using local storage"""
		now = time.time()
		window = self.local_storage[key]
        
		# Clean old entries
		while window and now - window[0] >= 60:
			window.popleft()
        
		if len(window) >= self.config.rate_limit.per_minute:
			reset = 60 - (int(now) % 60)
			return False, 0, reset
        
		window.append(now)
		remaining = self.config.rate_limit.per_minute - len(window)
		reset = 60 - (int(now) % 60)
        
		return True, remaining, reset
    
	async def cleanup_expired(self):
		"""Background task to clean expired rate limit entries"""
		while True:
			await asyncio.sleep(300)  # Every 5 minutes
			now = time.time()
            
			# Clean local storage
			expired_keys = []
			for key, window in self.local_storage.items():
				while window and now - window[0] >= 3600:
					window.popleft()
				if len(window) == 0:
					expired_keys.append(key)
            
			for key in expired_keys:
				del self.local_storage[key]

