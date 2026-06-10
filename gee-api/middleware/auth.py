"""
Authentication middleware and dependencies for the Arybit Geospatial Intelligence API.
"""

import hashlib
import time
from typing import Optional, Dict, Any
from collections import OrderedDict

import httpx
import jwt
from fastapi import Request, HTTPException, Depends
from starlette.middleware.base import BaseHTTPMiddleware

from config import get_config
from utils.exceptions import AuthenticationError
from utils.response import error_response


class AuthMiddleware(BaseHTTPMiddleware):
    """Authentication middleware with JWT, API keys, and internal service trust."""

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
        self.cache_ttl = self.config.auth_cache_ttl if hasattr(self.config, 'auth_cache_ttl') else 60
        self.max_cache_size = self.config.max_auth_cache_size if hasattr(self.config, 'max_auth_cache_size') else 10000

    def _get_jwt_secrets(self) -> list:
        secrets = []
        if self.config.auth.jwt_secret:
            secrets.append(self.config.auth.jwt_secret)
        if self.config.auth.jwt_secret_1:
            secrets.append(self.config.auth.jwt_secret_1)
        if self.config.auth.jwt_secret_2:
            secrets.append(self.config.auth.jwt_secret_2)
        return secrets

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        token = self._extract_token(request)

        if await self._check_internal_service(request):
            return await call_next(request)

        if not token:
            return error_response(
                error="Authentication required",
                error_code="AUTHENTICATION_REQUIRED",
                status_code=401
            )

        if token in self.config.auth.api_keys:
            request.state.user = {"user_id": "api_service", "role": "system"}
            return await call_next(request)

        try:
            user = await self._authenticate_token(token, request)
            request.state.user = user
            request.state.legal_organization_id = user.get("legal_organization_id", "1")
        except Exception as exc:
            return error_response(
                error=str(exc),
                error_code="AUTHENTICATION_FAILED",
                status_code=401
            )

        return await call_next(request)

    def _extract_token(self, request: Request) -> Optional[str]:
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header.replace("Bearer ", "").strip()
        return request.cookies.get("access_token")

    async def _check_internal_service(self, request: Request) -> bool:
        internal_secret = request.headers.get("X-Internal-Secret")
        internal_service = request.headers.get("X-Internal-Service", "").strip().lower()
        is_background = request.headers.get("X-Background-Service", "").lower() == "true"

        valid_secret = internal_secret and internal_secret == self.config.auth.internal_secret
        trusted_service = internal_service == self.config.auth.internal_service_name

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
        token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
        cached = self._get_from_cache(token_hash)
        if cached:
            return cached

        if self.config.auth.auth_mode == "local":
            user = self._decode_jwt_local(token)
        else:
            user = await self._authenticate_remote(token, request)

        self._add_to_cache(token_hash, user)
        return user

    def _decode_jwt_local(self, token: str) -> Dict[str, Any]:
        for secret in self.jwt_secrets:
            if not secret:
                continue
            try:
                payload = jwt.decode(token, secret, algorithms=[self.config.auth.jwt_algorithm])
                user_data = payload.get("data", {}) or {}
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
        original_ip = self._get_client_ip(request)

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
            user = data.get("user", {}) or {}
            user["legal_organization_id"] = user.get("legal_organization_id", "1")
            return user

    def _get_client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _get_from_cache(self, token_hash: str) -> Optional[Dict[str, Any]]:
        if token_hash in self.auth_cache:
            data, expiry = self.auth_cache[token_hash]
            if time.time() < expiry:
                self.auth_cache.move_to_end(token_hash)
                return data
            del self.auth_cache[token_hash]
        return None

    def _add_to_cache(self, token_hash: str, user: Dict[str, Any]):
        if len(self.auth_cache) >= self.max_cache_size:
            self.auth_cache.popitem(last=False)
        self.auth_cache[token_hash] = (user, time.time() + self.cache_ttl)
        self.auth_cache.move_to_end(token_hash)


async def get_current_user(request: Request) -> Dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user


async def require_verified_user(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if user.get("kyc_status") != "verified":
        raise HTTPException(status_code=403, detail="User verification required")
    return user
