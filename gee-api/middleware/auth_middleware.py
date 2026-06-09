"""
Authentication middleware for the geospatial intelligence API
"""

import hashlib
import time
from typing import Optional, Dict, Any
from collections import OrderedDict

import jwt
import httpx
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.middleware.base import BaseHTTPMiddleware

from config import get_config
from utils.exceptions import AuthenticationError, AuthorizationError
from utils.response import error_response


class AuthMiddleware(BaseHTTPMiddleware):
	"""Authentication middleware with JWT and API key support"""
    
	EXEMPT_PATHS = {
		"/", "/health", "/healthz", "/ready", "/ping",
		"/docs", "/openapi.json", "/redoc", "/metrics",
		"/api/satellites", "/api/indices"
	}
    
	def __init__(self, app):
		super().__init__(app)
		self.config = get_config()
		self.jwt_secrets = self._get_jwt_secrets()
		self.auth_cache = OrderedDict()
		self.cache_ttl = 60
		self.max_cache_size = 10000
    
	def _get_jwt_secrets(self) -> list:
		"""Get list of JWT secrets for rotation"""
		secrets = []
		if self.config.auth.jwt_secret:
			secrets.append(self.config.auth.jwt_secret)
		if self.config.auth.jwt_secret_1:
			secrets.append(self.config.auth.jwt_secret_1)
		if self.config.auth.jwt_secret_2:
			secrets.append(self.config.auth.jwt_secret_2)
		return secrets
    
	async def dispatch(self, request: Request, call_next):
		# Skip exempt paths
		if request.method == "OPTIONS" or request.url.path in self.EXEMPT_PATHS:
			return await call_next(request)
        
		# Extract token
		token = self._extract_token(request)
        
		# Check internal service authentication
		if await self._check_internal_service(request):
			return await call_next(request)
        
		# No token found
		if not token:
			return error_response(
				error="Authentication required",
				error_code="AUTHENTICATION_REQUIRED",
				status_code=401
			)
        
		# Check API keys
		if token in self.config.auth.api_keys:
			request.state.user = {"user_id": "api_service", "role": "system"}
			return await call_next(request)
        
		# Authenticate token
		try:
			user = await self._authenticate_token(token, request)
			request.state.user = user
			request.state.legal_organization_id = user.get("legal_organization_id", "1")
		except Exception as e:
			return error_response(
				error=str(e),
				error_code="AUTHENTICATION_FAILED",
				status_code=401
			)
        
		return await call_next(request)
    
	def _extract_token(self, request: Request) -> Optional[str]:
		"""Extract token from Authorization header or cookie"""
		auth_header = request.headers.get("Authorization")
		if auth_header and auth_header.startswith("Bearer "):
			return auth_header.replace("Bearer ", "").strip()
        
		return request.cookies.get("access_token")
    
	async def _check_internal_service(self, request: Request) -> bool:
		"""Check if request is from trusted internal service"""
		internal_secret = request.headers.get("X-Internal-Secret")
		internal_service = request.headers.get("X-Internal-Service", "").strip().lower()
		is_background = request.headers.get("X-Background-Service", "").lower() == "true"
        
		valid_secret = internal_secret and internal_secret == self.config.auth.internal_secret
		trusted_service = internal_service in self.config.auth.internal_service_name
        
		if valid_secret and is_background and trusted_service:
			request.state.user = {
				"user_id": internal_service,
				"role": "system",
				"kyc_status": "verified",
				"is_system": True
			}
			return True
        
		return False
    
	async def _authenticate_token(self, token: str, request: Request) -> Dict[str, Any]:
		"""Authenticate token via remote auth service or local JWT"""
		# Check cache
		token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
		cached = self._get_from_cache(token_hash)
		if cached:
			return cached
        
		if self.config.auth.auth_mode == "local":
			user = self._decode_jwt_local(token)
		else:
			user = await self._authenticate_remote(token, request)
        
		# Cache result
		self._add_to_cache(token_hash, user)
        
		return user
    
	def _decode_jwt_local(self, token: str) -> Dict[str, Any]:
		"""Decode JWT locally (for development/test)"""
		for secret in self.jwt_secrets:
			if not secret:
				continue
			try:
				payload = jwt.decode(token, secret, algorithms=[self.config.auth.jwt_algorithm])
				user_data = payload.get("data", {})
				return {
					"user_id": payload.get("sub"),
					"username": user_data.get("username"),
					"email": user_data.get("email"),
					"role": user_data.get("role", "user"),
					"kyc_status": user_data.get("kyc_status", "verified"),
					"legal_organization_id": user_data.get("legal_organization_id", "1")
				}
			except jwt.InvalidTokenError:
				continue
        
		raise AuthenticationError("Invalid or expired token")
    
	async def _authenticate_remote(self, token: str, request: Request) -> Dict[str, Any]:
		"""Authenticate via remote auth service"""
		original_ip = self._get_client_ip(request)
		original_ua = request.headers.get("user-agent", "Unknown")
        
		headers = {
			"Authorization": f"Bearer {token}",
			"X-Forwarded-For": original_ip,
			"X-Real-IP": original_ip,
			"X-Internal-Service": self.config.auth.internal_service_name,
			"X-Internal-Secret": self.config.auth.internal_secret
		}
        
		async with httpx.AsyncClient(timeout=10.0) as client:
			response = await client.get(
				f"{self.config.auth.auth_api_base}/users/me",
				headers=headers
			)
            
			if response.status_code == 401:
				raise AuthenticationError("Invalid or expired token")
            
			response.raise_for_status()
			data = response.json()
            
			user = data.get("user", {})
			user["legal_organization_id"] = user.get("legal_organization_id", "1")
            
			return user
    
	def _get_client_ip(self, request: Request) -> str:
		"""Get client IP address"""
		forwarded = request.headers.get("X-Forwarded-For")
		if forwarded:
			return forwarded.split(",")[0].strip()
		return request.client.host if request.client else "unknown"
    
	def _get_from_cache(self, token_hash: str) -> Optional[Dict]:
		"""Get cached authentication result"""
		if token_hash in self.auth_cache:
			data, expiry = self.auth_cache[token_hash]
			if time.time() < expiry:
				self.auth_cache.move_to_end(token_hash)
				return data
			del self.auth_cache[token_hash]
		return None
    
	def _add_to_cache(self, token_hash: str, user: Dict):
		"""Add authentication result to cache"""
		if len(self.auth_cache) >= self.max_cache_size:
			self.auth_cache.popitem(last=False)
		self.auth_cache[token_hash] = (user, time.time() + self.cache_ttl)
		self.auth_cache.move_to_end(token_hash)

