"""
Authentication Middleware for Geospatial Intelligence API
Multi-layer authentication with JWT, API keys, and internal service trust
"""

import hashlib
import time
from typing import Dict, Optional, List
from collections import OrderedDict
import threading
import jwt
import httpx
import ipaddress
import os

from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import get_config
from utils.exceptions import AuthenticationError
from utils.response import error_response
from logging_config import get_logger

logger = get_logger(__name__)


def parse_trusted_proxies():
    networks = []
    config = get_config()
    for item in config.trusted_proxies.split(","):
        try:
            networks.append(ipaddress.ip_network(item.strip()))
        except ValueError:
            continue
    return networks


TRUSTED_NETWORKS = parse_trusted_proxies()
INTERNAL_SERVICE_SECRET = get_config().auth.internal_secret
JWT_SECRETS = [
    secret
    for secret in [
        get_config().auth.jwt_secret,
        get_config().auth.jwt_secret_1,
        get_config().auth.jwt_secret_2,
    ]
    if secret
]


class BoundedAuthCache:
    """Thread-safe bounded LRU cache for auth tokens"""

    def __init__(self, maxsize: int = 10000, ttl: int = 60):
        self.cache = OrderedDict()
        self.maxsize = maxsize
        self.ttl = ttl
        self.timestamps = {}
        self.lock = threading.RLock()

    def get(self, key: str) -> Optional[Dict]:
        with self.lock:
            if key in self.cache:
                if time.time() - self.timestamps.get(key, 0) > self.ttl:
                    self.cache.pop(key, None)
                    self.timestamps.pop(key, None)
                    return None
                self.cache.move_to_end(key)
                return self.cache[key]
            return None

    def set(self, key: str, value: Dict):
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            else:
                if len(self.cache) >= self.maxsize:
                    oldest = next(iter(self.cache))
                    self.cache.pop(oldest)
                    self.timestamps.pop(oldest, None)
                self.cache[key] = value
            self.timestamps[key] = time.time()

    def clear_expired(self):
        with self.lock:
            now = time.time()
            expired = [k for k, ts in self.timestamps.items() if now - ts > self.ttl]
            for k in expired:
                self.cache.pop(k, None)
                self.timestamps.pop(k, None)


auth_cache = BoundedAuthCache(maxsize=10000, ttl=60)


def resolve_client_ip(request: Request) -> str:
    """Resolve client IP with proxy support"""
    client_ip = getattr(request.client, "host", "") or "unknown"
    try:
        addr = ipaddress.ip_address(client_ip)
        is_trusted = any(addr in net for net in TRUSTED_NETWORKS)
    except ValueError:
        is_trusted = False

    if is_trusted:
        xff = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if xff:
            return xff
    return client_ip


def decode_jwt_with_rotation(token: str) -> dict:
    """Try each configured secret until one verifies"""
    for secret in JWT_SECRETS:
        if not secret:
            continue
        try:
            return jwt.decode(
                token,
                secret,
                algorithms=[get_config().auth.jwt_algorithm],
                audience=["aarab-api", "arybit-gateway", "geo-api"],
                options={"verify_aud": False}
            )
        except jwt.InvalidTokenError:
            continue
    raise jwt.InvalidTokenError("All JWT secrets failed verification")


async def authenticate_remote(token: str, request: Request) -> Dict:
    """Authenticate via remote auth service with caching"""
    token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]

    cached = auth_cache.get(token_hash)
    if cached:
        return cached

    async with httpx.AsyncClient(timeout=10.0) as client:
        original_ip = resolve_client_ip(request)
        original_ua = request.headers.get("user-agent", "Unknown")

        headers = {
            "Authorization": f"Bearer {token}",
            "X-Forwarded-For": original_ip,
            "X-Real-IP": original_ip,
            "X-Internal-Service": get_config().auth.internal_service_name,
            "X-Internal-Secret": INTERNAL_SERVICE_SECRET or "",
            "X-Original-IP": original_ip,
            "X-Original-UA": original_ua,
        }

        response = await client.get(
            f"{get_config().auth.auth_api_base}/users/me",
            headers=headers,
            timeout=10.0
        )

        if response.status_code == 401:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        response.raise_for_status()
        data = response.json()

        auth_cache.set(token_hash, data)
        return data


class SecurityMiddleware(BaseHTTPMiddleware):
    """Block known malicious IPs and scanner paths before auth"""

    NOISY_PATHS = {
        "/", "/.env", "/.git", "/wp-admin", "/wp-login.php",
        "/phpmyadmin", "/xmlrpc.php", "/debug", "/actuator",
        "/vendor", "/composer.json", "/package.json"
    }
    BLOCKED_IPS = {ip.strip() for ip in os.getenv("BLOCKED_IPS", "").split(",") if ip.strip()}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        is_noisy = any(path.startswith(p) for p in self.NOISY_PATHS)
        request.state.is_noisy = is_noisy

        if is_noisy and path not in ["/health", "/healthz", "/ready", "/ping"]:
            logger.warning(f"Blocked scanner path: {path} from {resolve_client_ip(request)}")
            return JSONResponse(status_code=403, content=error_response("Forbidden", 403))

        if self.BLOCKED_IPS:
            client_ip = resolve_client_ip(request)
            if client_ip in self.BLOCKED_IPS:
                logger.warning(f"Blocked request from blocked IP: {client_ip}")
                return JSONResponse(status_code=403, content=error_response("Forbidden", 403))

        return await call_next(request)


class AuthMiddleware(BaseHTTPMiddleware):
    """Multi-layer authentication middleware"""

    EXEMPT_PATHS = {
        "/", "/health", "/healthz", "/ready", "/ping",
        "/docs", "/openapi.json", "/redoc", "/metrics",
        "/api/satellites", "/api/indices"
    }
    TRUSTED_BACKGROUND_SERVICES = {
        "arybit-geo-intelligence",
        "arybit-autonomous-research-agent-bot",
        "arybit-worker",
        "arybit-ai-gateway"
    }

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        token = None
        source = "None"

        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.replace("Bearer ", "").strip()
            source = "Header"
        else:
            token = request.cookies.get("access_token")
            source = "Cookie" if token else "None"

        internal_secret = request.headers.get("X-Internal-Secret")
        internal_service = request.headers.get("X-Internal-Service", "").strip().lower()
        is_background_service = request.headers.get("X-Background-Service", "").lower() == "true"
        has_valid_internal_secret = internal_secret and internal_secret == INTERNAL_SERVICE_SECRET

        if has_valid_internal_secret and is_background_service and internal_service in self.TRUSTED_BACKGROUND_SERVICES and not token:
            request.state.user = {
                "user_id": internal_service,
                "username": internal_service,
                "role": "system",
                "kyc_status": "verified",
                "is_system": True,
                "is_verified": True,
            }
            org_id = request.headers.get("X-Organization-ID")
            request.state.legal_organization_id = org_id if org_id and org_id.isdigit() else "1"
            request.state.api_key = f"internal:{internal_service}"
            logger.info(f"Internal service authenticated: {internal_service}")
            return await call_next(request)

        if not token:
            logger.warning(f"No token provided for {request.url.path} from {resolve_client_ip(request)}")
            return JSONResponse(status_code=401, content=error_response("Authentication required", 401))

        if token in get_config().auth.api_keys:
            request.state.user = {"user_id": "api_service", "role": "system", "is_verified": True}
            request.state.api_key = token
            return await call_next(request)

        try:
            identity = await authenticate_remote(token, request)
            request.state.user = identity.get("user", {})
            request.state.legal_organization_id = identity.get("user", {}).get("legal_organization_id", "1")
            request.state.identity = identity
        except HTTPException as e:
            logger.warning(f"Auth failed for {request.url.path}: {e.detail}")
            return JSONResponse(status_code=e.status_code, content=error_response(e.detail, e.status_code))
        except Exception as e:
            logger.error(f"Auth service error: {e}")
            return JSONResponse(status_code=503, content=error_response("Authentication service unavailable", 503))

        return await call_next(request)
