"""
Arybit Geospatial Intelligence CORE - Complete Production Platform
Integrates: Earth Engine, Gemini AI, Full Authentication, Document Intelligence, AARAB Agents
Addresses: Memory leaks, async blocking, error handling, Redis management
"""

from __future__ import annotations

import os
import json
import uuid
import asyncio
import logging
import hashlib
import time
import threading
import io
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, AsyncGenerator, Tuple, Set
from functools import lru_cache, wraps
from contextlib import asynccontextmanager
from collections import defaultdict, deque, OrderedDict
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import tempfile
import base64
from enum import Enum
import random

# Core dependencies
import ee
import numpy as np
import httpx
import jwt
from fastapi import FastAPI, HTTPException, Request, Depends, BackgroundTasks, status, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, ConfigDict, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Counter, Gauge, Histogram, REGISTRY
import redis.asyncio as aioredis
import uvicorn
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.background import BackgroundTask
from utils.google_embeddings import google_embeddings

# Control heavy library imports to prevent startup hangs/crashes
DISABLE_HEAVY_IMPORTS = os.environ.get('DISABLE_HEAVY_IMPORTS', 'false').lower() == 'true'
DISABLE_SENTENCE_TRANSFORMERS = os.environ.get('DISABLE_SENTENCE_TRANSFORMERS', 'false').lower() == 'true' or DISABLE_HEAVY_IMPORTS

# Google AI - Gemini
GOOGLE_AI_AVAILABLE = False
try:
    if not DISABLE_HEAVY_IMPORTS:
        from google import genai
        GOOGLE_AI_AVAILABLE = True
except Exception:
    GOOGLE_AI_AVAILABLE = False

# Optional dependencies with graceful fallback
try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import pytesseract
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False


try:
    import fitz  # PyMuPDF
    FITZ_AVAILABLE = True
except ImportError:
    FITZ_AVAILABLE = False

SENTENCE_TRANSFORMERS_AVAILABLE = False
if not DISABLE_SENTENCE_TRANSFORMERS:
    try:
        from sentence_transformers import SentenceTransformer
        SENTENCE_TRANSFORMERS_AVAILABLE = True
    except Exception:
        SENTENCE_TRANSFORMERS_AVAILABLE = False

try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

try:
    from docx import Document
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False

from dotenv import load_dotenv
load_dotenv()


# ============================================================
# CONFIGURATION WITH PRODUCTION HARDENING
# ============================================================

class GeospatialSettings(BaseSettings):
    """Geospatial intelligence configuration with production hardening"""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    
    # API Configuration
    app_name: str = "Arybit Geospatial Intelligence"
    app_version: str = "2.0.0"
    environment: str = "production"
    auth_mode: str = "remote"
    
    # Google Earth Engine
    gee_service_account: str = ""
    gee_private_key: str = ""
    gee_project_id: str = ""
    
    # Google Cloud
    gcp_project_id: str = ""
    gcp_location: str = "us-central1"
    bigquery_dataset: str = "geospatial_analytics"
    
    # Google Gemini AI
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash-exp"
    gemini_vision_model: str = "gemini-2.0-flash-exp"
    gemini_temperature: float = 0.7
    gemini_max_output_tokens: int = 8192
    gemini_top_p: float = 0.95
    gemini_top_k: int = 40
    
    # Authentication
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_secret_1: Optional[str] = None
    jwt_secret_2: Optional[str] = None
    auth_api_base: str = "https://api.arybit.co.ke"
    auth_internal_base: str = "http://auth-service:8001"
    ai_gateway_internal_secret: str = ""
    internal_service_name: str = "arybit-geo-intelligence"
    
    # API Keys
    api_keys: List[str] = []
    
    # Rate Limiting (Production tuned)
    rate_limit_per_minute: int = 60
    rate_limit_anonymous_per_minute: int = 30
    global_max_concurrent_requests: int = 50
    
    # Bounded Queue Limits (Prevents memory leaks)
    max_conversations: int = 10000
    max_messages_per_convo: int = 50
    conversation_ttl_seconds: int = 3600
    max_rate_limit_entries: int = 50000
    max_auth_cache_size: int = 10000
    auth_cache_ttl: int = 60
    
    # Model thresholds
    ndvi_water_threshold: float = 0.0
    ndvi_sparse_threshold: float = 0.2
    ndvi_moderate_threshold: float = 0.4
    ndvi_dense_threshold: float = 0.6
    
    # Chunking configuration
    chunk_size: int = 1500
    chunk_overlap: int = 200
    legal_chunk_size: int = 2048
    legal_chunk_overlap: int = 256
    semantic_chunk_threshold: float = 0.45
    embedding_batch_size: int = 10
    max_concurrent_embedding_batches: int = 5
    embedding_model: str = "nomic-embed-text"
    embedding_cache_max_size: int = 1000

    # Google Embeddings Config
    GOOGLE_EMBEDDING_MODEL: str = "text-embedding-004"
    GOOGLE_EMBEDDING_TASK_TYPE: str = "RETRIEVAL_DOCUMENT"
    GOOGLE_EMBEDDING_BATCH_SIZE: int = 10
    GOOGLE_EMBEDDING_MAX_RETRIES: int = 3
    GOOGLE_EMBEDDING_TIMEOUT: int = 30

    # Embedding Cache
    EMBEDDING_CACHE_MAX_SIZE: int = 10000
    EMBEDDING_CACHE_TTL: int = 86400

    # Document processing
    max_document_size_mb: int = 50
    document_processing_timeout: int = 300
    ocr_enabled: bool = True
    max_documents_per_day: int = 10
    grace_max_doc_size_mb: int = 10
    dead_letter_ttl_seconds: int = 604800
    ollama_host: str = "http://localhost:11434"
    ollama_timeout: int = 120
    allowed_extensions: Set[str] = {
        '.pdf', '.docx', '.doc', '.txt', '.csv', '.rtf',
        '.png', '.jpg', '.jpeg', '.gif', '.webp', '.tiff',
        '.mp4', '.avi', '.mov', '.mkv',
        '.mp3', '.wav', '.ogg', '.m4a',
        '.py', '.js', '.ts', '.java', '.cpp', '.go', '.rs',
        '.json', '.xml', '.yaml', '.md'
    }
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: Optional[str] = None
    redis_url: Optional[str] = None
    redis_max_connections: int = 50
    redis_socket_timeout: int = 10
    redis_connect_timeout: int = 5
    cache_ttl_seconds: int = 3600
    
    # Thread pool for CPU-bound operations
    cpu_executor_threads: int = 4
    
    # Logging
    log_level: str = "INFO"
    log_format: str = "json"
    
    # Trusted proxies
    trusted_proxies: str = "127.0.0.1,::1,10.0.0.0/8"
    
    # Grace mode limits (for unverified users)
    grace_max_tokens: int = 4096
    grace_max_prompt_chars: int = 16000
    grace_max_documents_per_day: int = 5
    grace_max_doc_size_mb: int = 10
    
    # Allowed models for grace mode
    grace_allowed_models: List[str] = ["gemini-2.0-flash-exp"]
    
    # Circuit breaker configuration
    circuit_breaker_threshold: int = 5
    circuit_breaker_timeout: int = 30
    
    def get_gee_credentials(self):
        if self.gee_service_account and self.gee_private_key:
            if self.gee_private_key.strip().startswith('{'):
                return ee.ServiceAccountCredentials(self.gee_service_account, key_data=self.gee_private_key)
            return ee.ServiceAccountCredentials(self.gee_service_account, key_file=self.gee_private_key)
        return None


settings = GeospatialSettings()


# ============================================================
# PRODUCTION LOGGING SETUP
# ============================================================

class RequestContextLogger:
    """Context-aware logger with request ID propagation"""
    
    _context = {}
    
    @classmethod
    def set_request_id(cls, request_id: str):
        cls._context['request_id'] = request_id
    
    @classmethod
    def get_request_id(cls) -> str:
        return cls._context.get('request_id', 'unknown')
    
    @classmethod
    def clear(cls):
        cls._context.clear()
    
    @classmethod
    def log(cls, level: str, msg: str, **kwargs):
        request_id = cls.get_request_id()
        log_msg = f"[{request_id}] {msg}"
        if kwargs:
            log_msg += f" | {json.dumps(kwargs, default=str)}"
        getattr(logging, level)(log_msg)


# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# ============================================================
# BOUNDED QUEUES (Prevents Memory Leaks)
# ============================================================

class BoundedDeque(deque):
    """Deque with maximum size limit"""
    def __init__(self, maxlen: int, ttl_seconds: Optional[int] = None):
        super().__init__(maxlen=maxlen)
        self.ttl_seconds = ttl_seconds
        self.timestamps = deque(maxlen=maxlen)
    
    def append(self, x):
        super().append(x)
        if self.ttl_seconds:
            self.timestamps.append(time.time())
    
    def clean_expired(self, now: float) -> int:
        """Remove expired items and return count removed"""
        if not self.ttl_seconds:
            return 0
        removed = 0
        while self.timestamps and now - self.timestamps[0] > self.ttl_seconds:
            self.popleft()
            self.timestamps.popleft()
            removed += 1
        return removed


class BoundedLRUCache:
    """Bounded LRU cache with TTL support"""
    
    def __init__(self, maxsize: int = 1000, ttl_seconds: int = 3600):
        self.cache = OrderedDict()
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self.timestamps = {}
        self.lock = threading.RLock()
    
    def get(self, key: str) -> Optional[Any]:
        with self.lock:
            if key in self.cache:
                # Check TTL
                if self.ttl_seconds and (time.time() - self.timestamps.get(key, 0)) > self.ttl_seconds:
                    self.cache.pop(key, None)
                    self.timestamps.pop(key, None)
                    return None
                self.cache.move_to_end(key)
                return self.cache[key]
            return None
    
    def set(self, key: str, value: Any):
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
    
    def delete(self, key: str):
        with self.lock:
            self.cache.pop(key, None)
            self.timestamps.pop(key, None)
    
    def clear_expired(self):
        with self.lock:
            now = time.time()
            expired = [k for k, ts in self.timestamps.items() 
                      if now - ts > self.ttl_seconds]
            for k in expired:
                self.cache.pop(k, None)
                self.timestamps.pop(k, None)


# Initialize bounded caches
conversation_store = BoundedLRUCache(
    maxsize=settings.max_conversations,
    ttl_seconds=settings.conversation_ttl_seconds
)
rate_limit_store = defaultdict(lambda: BoundedDeque(maxlen=settings.rate_limit_per_minute, ttl_seconds=60))
auth_cache = BoundedLRUCache(
    maxsize=settings.max_auth_cache_size,
    ttl_seconds=settings.auth_cache_ttl
)

# Cache for document embeddings
embedding_cache = BoundedLRUCache(maxsize=10000, ttl_seconds=3600)


# ============================================================
# THREAD POOL FOR CPU-BOUND OPERATIONS
# ============================================================

class CPUExecutor:
    """Thread pool executor for CPU-bound operations"""
    
    _instance = None
    _executor: Optional[ThreadPoolExecutor] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def get_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=settings.cpu_executor_threads,
                thread_name_prefix="cpu_worker"
            )
        return self._executor
    
    async def run(self, func, *args, **kwargs):
        """Run CPU-bound function in thread pool"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self.get_executor(), lambda: func(*args, **kwargs))
    
    def shutdown(self):
        if self._executor:
            self._executor.shutdown(wait=True)

cpu_executor = CPUExecutor()


# ============================================================
# CIRCUIT BREAKER IMPLEMENTATION
# ============================================================

class CircuitBreaker:
    """Circuit breaker for external service calls"""
    
    def __init__(self, name: str, threshold: int = 5, timeout: int = 30):
        self.name = name
        self.threshold = threshold
        self.timeout = timeout
        self.failures = 0
        self.last_failure = 0.0
        self.lock = asyncio.Lock()
    
    async def call(self, func, *args, **kwargs):
        """Execute function with circuit breaker protection"""
        async with self.lock:
            if self.failures >= self.threshold:
                elapsed = time.time() - self.last_failure
                if elapsed < self.timeout:
                    logger.warning(f"Circuit breaker '{self.name}' is OPEN (failures={self.failures})")
                    raise HTTPException(503, f"Service '{self.name}' temporarily unavailable")
                # Half-open state - allow one request
                logger.info(f"Circuit breaker '{self.name}' half-open - allowing probe")
                self.failures = self.threshold - 1
        
        try:
            result = await func(*args, **kwargs)
            await self.record_success()
            return result
        except Exception as e:
            await self.record_failure()
            raise
    
    async def record_success(self):
        async with self.lock:
            if self.failures > 0:
                logger.info(f"Circuit breaker '{self.name}' CLOSED after success")
            self.failures = 0
    
    async def record_failure(self):
        async with self.lock:
            self.failures += 1
            self.last_failure = time.time()
            logger.warning(f"Circuit breaker '{self.name}' failure {self.failures}/{self.threshold}")


# Initialize circuit breakers
auth_circuit_breaker = CircuitBreaker("auth", threshold=3, timeout=30)
gemini_circuit_breaker = CircuitBreaker("gemini", threshold=5, timeout=60)
gee_circuit_breaker = CircuitBreaker("earth_engine", threshold=3, timeout=30)
ollama_circuit_breaker = CircuitBreaker("ollama", threshold=3, timeout=30)
redis_circuit_breaker = CircuitBreaker("redis", threshold=3, timeout=30)


# ============================================================
# PROMETHEUS METRICS
# ============================================================

_metric_lock = threading.Lock()

def safe_counter(name: str, documentation: str, labelnames: list = None):
    if labelnames is None: labelnames = []
    with _metric_lock:
        try:
            return REGISTRY._names_to_collectors[name]
        except KeyError:
            return Counter(name, documentation, labelnames, registry=REGISTRY)

def safe_gauge(name: str, documentation: str, labelnames: list = None):
    if labelnames is None: labelnames = []
    with _metric_lock:
        try:
            return REGISTRY._names_to_collectors[name]
        except KeyError:
            return Gauge(name, documentation, labelnames, registry=REGISTRY)

def safe_histogram(name: str, documentation: str, labelnames: list = None, buckets=None):
    if labelnames is None: labelnames = []
    if buckets is None:
        buckets = [0.1, 0.5, 1, 2, 5, 10, 30, 60, 120]
    with _metric_lock:
        try:
            return REGISTRY._names_to_collectors[name]
        except KeyError:
            return Histogram(name, documentation, labelnames, buckets=buckets, registry=REGISTRY)

# Metrics
auth_requests_total = safe_counter("geo_auth_requests_total", "Total authentication requests", ["method", "status"])
auth_failures_total = safe_counter("geo_auth_failures_total", "Total authentication failures", ["reason"])
api_requests_total = safe_counter("geo_api_requests_total", "Total API requests", ["endpoint", "user_id", "status"])
api_request_duration = safe_histogram("geo_api_request_duration_seconds", "API request duration", ["endpoint", "method"])
gemini_requests_total = safe_counter("geo_gemini_requests_total", "Total Gemini AI requests", ["model", "operation", "status"])
gemini_request_duration = safe_histogram("geo_gemini_request_duration_seconds", "Gemini request duration", ["model"])
gee_requests_total = safe_counter("geo_gee_requests_total", "Total Earth Engine requests", ["operation", "status"])
cache_hits_total = safe_counter("geo_cache_hits_total", "Total cache hits", ["cache_type"])
cache_misses_total = safe_counter("geo_cache_misses_total", "Total cache misses", ["cache_type"])
circuit_breaker_state = safe_gauge("geo_circuit_breaker_state", "Circuit breaker state (0=closed, 1=open)", ["breaker"])
active_requests_gauge = safe_gauge("geo_active_requests", "Currently active requests", ["endpoint"])
document_chunks_indexed = safe_counter("geo_document_chunks_indexed", "Document chunks indexed", ["org_id"])
kyc_grace_requests = safe_counter("geo_kyc_grace_requests_total", "Grace mode requests", ["path"])
kyc_blocked_requests = safe_counter("geo_kyc_blocked_requests_total", "Blocked grace requests", ["path", "reason"])
document_queue_depth = safe_gauge("geo_document_queue_depth", "Pending document processing tasks")
inference_duration = safe_histogram("geo_inference_duration_seconds", "AI inference duration", ["model", "type"])


# ============================================================
# AUTHENTICATION MIDDLEWARE (Production Hardened)
# ============================================================

def parse_trusted_proxies():
    networks = []
    for item in settings.trusted_proxies.split(","):
        try:
            networks.append(ipaddress.ip_network(item.strip()))
        except ValueError:
            continue
    return networks

TRUSTED_NETWORKS = parse_trusted_proxies()
INTERNAL_SERVICE_SECRET = settings.ai_gateway_internal_secret
JWT_SECRETS = [s for s in [settings.jwt_secret, settings.jwt_secret_1, settings.jwt_secret_2] if s]


NOISY_PATHS = {
    "/", "/health", "/healthz", "/ready", "/ping",
    "/favicon.ico", "/metrics", "/docs", "/openapi.json", "/redoc",
    "/.env", "/.git", "/wp-admin", "/phpmyadmin", "/xmlrpc.php"
}

BLOCKED_IPS = {ip.strip() for ip in os.getenv("BLOCKED_IPS", "").split(",") if ip.strip()}
TRUSTED_BACKGROUND_SERVICES = {
    "arybit-geo-intelligence",
    "arybit-autonomous-research-agent-bot",
    "arybit-worker",
}


def resolve_client_ip(request: Request) -> str:
    client_ip = getattr(request.client, "host", "") or "unknown"
    try:
        addr = ipaddress.ip_address(client_ip)
        is_trusted = any(addr in net for net in TRUSTED_NETWORKS)
    except ValueError:
        is_trusted = False
    if is_trusted:
        xff = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if xff:
            return xff
    return client_ip


def decode_jwt_with_rotation(token: str) -> dict:
    for secret in JWT_SECRETS:
        if not secret:
            continue
        try:
            return jwt.decode(token, secret, algorithms=[settings.jwt_algorithm])
        except jwt.InvalidTokenError:
            continue
    raise jwt.InvalidTokenError("All JWT secrets failed verification")


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        is_noisy = any(path.startswith(p) for p in NOISY_PATHS)
        request.state.is_noisy = is_noisy
        
        if BLOCKED_IPS:
            client_ip = resolve_client_ip(request)
            if client_ip in BLOCKED_IPS:
                logger.warning(f"Blocked request from {client_ip}")
                return JSONResponse(status_code=403, content={"error": "Forbidden"})
        
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in ["/health", "/healthz", "/ready", "/ping", "/metrics"]:
            return await call_next(request)

        user = getattr(request.state, "user", {})
        user_id = user.get("user_id") if isinstance(user, dict) else None

        if user_id:
            key = f"user:{user_id}"
            limit = settings.rate_limit_per_minute
        else:
            key = f"anon:{resolve_client_ip(request)}"
            limit = settings.rate_limit_anonymous_per_minute

        now = time.time()

        if redis_client:
            minute_key = f"rl:{key}:{int(now // 60)}"
            count = await safe_redis_op(redis_client.incr(minute_key), default=0)
            if count == 1:
                await safe_redis_op(redis_client.expire(minute_key, 60))
            if count and int(count) > limit:
                return JSONResponse(status_code=429, content={"error": "Rate limit exceeded. Please wait 60 seconds."})
        else:
            window = rate_limit_store[key]
            window.clean_expired(now)
            if len(window) >= limit:
                return JSONResponse(status_code=429, content={"error": "Rate limit exceeded. Please wait 60 seconds."})
            window.append(now)

        return await call_next(request)


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        RequestContextLogger.set_request_id(request_id)
        
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        active_requests_gauge.labels(endpoint=request.url.path).inc()
        
        try:
            response = await call_next(request)
            duration = (time.perf_counter() - start) * 1000
            
            user_id = getattr(request.state, "user", {}).get("user_id", "anonymous")
            RequestContextLogger.log("info", f"ACCESS {request.method} {request.url.path} status={response.status_code} duration={duration:.1f}ms user={user_id}")
            
            api_requests_total.labels(
                endpoint=request.url.path.split('?')[0],
                user_id=str(user_id)[:30],
                status=str(response.status_code)
            ).inc()
            
            api_request_duration.labels(
                endpoint=request.url.path.split('?')[0],
                method=request.method
            ).observe(duration / 1000)
            
            return response
        finally:
            active_requests_gauge.labels(endpoint=request.url.path).dec()
            RequestContextLogger.clear()


class AuthMiddleware(BaseHTTPMiddleware):
    EXEMPT_PATHS = {
        "/", "/health", "/healthz", "/ready", "/ping",
        "/docs", "/openapi.json", "/redoc", "/metrics",
        "/api/satellites", "/api/indices"
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
        
        # Internal service authentication
        internal_secret = request.headers.get("X-Internal-Secret")
        internal_service = request.headers.get("X-Internal-Service", "").strip().lower()
        is_background_service = request.headers.get("X-Background-Service", "").lower() == "true"
        has_valid_internal_secret = internal_secret and internal_secret == INTERNAL_SERVICE_SECRET
        
        if has_valid_internal_secret and is_background_service and internal_service in TRUSTED_BACKGROUND_SERVICES and not token:
            request.state.user = {
                "user_id": internal_service,
                "username": internal_service,
                "role": "system",
                "kyc_status": "verified",
                "is_system": True,
                "is_verified": True
            }
            org_id = request.headers.get("X-Organization-ID")
            request.state.legal_organization_id = org_id if org_id and org_id.isdigit() else "1"
            request.state.api_key = f"internal:{internal_service}"
            RequestContextLogger.info(f"Internal service authenticated: {internal_service}")
            return await call_next(request)
        
        if not token:
            auth_failures_total.labels(reason="no_token").inc()
            return JSONResponse(status_code=401, content={"error": "Authentication required"})
        
        # API key authentication
        if token in settings.api_keys:
            request.state.user = {"user_id": "api_service", "role": "system", "is_verified": True}
            request.state.api_key = token
            return await call_next(request)
        
        # Remote JWT authentication
        try:
            async def _auth():
                return await authenticate_remote(token, request)
            
            identity = await auth_circuit_breaker.call(_auth)
            request.state.user = identity.get("user", {})
            request.state.legal_organization_id = identity.get("user", {}).get("legal_organization_id", "1")
            auth_requests_total.labels(method=source, status="success").inc()
            
        except HTTPException as e:
            auth_requests_total.labels(method=source, status="failed").inc()
            auth_failures_total.labels(reason="invalid_token").inc()
            return JSONResponse(status_code=e.status_code, content={"error": e.detail})
        except Exception as e:
            logger.error(f"Authentication error: {e}")
            return JSONResponse(status_code=503, content={"error": "Authentication service unavailable"})
        
        return await call_next(request)


async def authenticate_remote(token: str, request: Request) -> Dict[str, Any]:
    """Authenticate via remote auth service with caching"""
    token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
    
    # Check cache
    cached = auth_cache.get(token_hash)
    if cached:
        cache_hits_total.labels(cache_type="auth").inc()
        return cached
    
    cache_misses_total.labels(cache_type="auth").inc()
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        original_ip = resolve_client_ip(request)
        original_ua = request.headers.get("user-agent", "Unknown")
        
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Forwarded-For": original_ip,
            "X-Real-IP": original_ip,
            "X-Internal-Service": settings.internal_service_name,
            "X-Internal-Secret": INTERNAL_SERVICE_SECRET or "",
            "X-Original-IP": original_ip,
            "X-Original-UA": original_ua
        }
        
        response = await client.get(f"{settings.auth_api_base}/users/me", headers=headers, timeout=10.0)
        
        if response.status_code == 401:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        
        response.raise_for_status()
        data = response.json()
        
        # Cache successful response
        auth_cache.set(token_hash, data)
        
        return data


async def get_current_user(request: Request) -> Dict[str, Any]:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


async def require_verified_user(request: Request) -> Dict[str, Any]:
    user = await get_current_user(request)
    is_verified = user.get("is_verified") or user.get("kyc_status") in ["verified", "approved", "verified_institutional"]
    
    if not is_verified and user.get("role") != "system":
        raise HTTPException(status_code=403, detail={
            "error": "KYC_VERIFICATION_REQUIRED",
            "message": "KYC verification required for geospatial analysis",
            "action": "https://account.arybit.co.ke/auth/verify-code"
        })
    return user


async def apply_grace_limits(request: Request, user: dict, estimated_tokens: int, model: str = None):
    """Apply grace limits for unverified users"""
    is_verified = user.get("is_verified") or user.get("kyc_status") in ["verified", "approved", "verified_institutional"]
    if is_verified or user.get("role") == "system":
        return
    
    model_to_check = model or getattr(request.state, "model", settings.gemini_model)
    if model_to_check not in settings.grace_allowed_models:
        raise HTTPException(403, f"Model '{model_to_check}' requires KYC verification")
    
    identity_key = request_identity_key(request, user)
    current_usage = await get_user_usage(identity_key)
    
    if current_usage + estimated_tokens > settings.grace_max_tokens:
        kyc_blocked_requests.labels(path=request.url.path, reason="limit_exceeded").inc()
        raise HTTPException(403, detail={
            "error": "GRACE_LIMIT_EXCEEDED",
            "message": f"Daily limit of {settings.grace_max_tokens} tokens reached",
            "used": current_usage,
            "limit": settings.grace_max_tokens,
            "action": "https://account.arybit.co.ke/auth/verify-code"
        })
    
    kyc_grace_requests.labels(path=request.url.path).inc()
    request.state.kyc_grace_mode = True


def build_identity_key(user_id: Optional[str], api_key: Optional[str], org_id: Optional[str] = None) -> str:
    org_prefix = f"org:{org_id}:" if org_id else ""
    if user_id:
        return f"{org_prefix}user:{user_id}"
    if api_key:
        return f"{org_prefix}key:{api_key[:16]}"
    return f"{org_prefix}anonymous"


def request_identity_key(request: Request, user: Optional[dict] = None) -> str:
    user_id = user.get("user_id") if isinstance(user, dict) else None
    org_id = getattr(request.state, "legal_organization_id", None)
    api_key = getattr(request.state, "api_key", None)
    return build_identity_key(str(user_id) if user_id else None, api_key, str(org_id) if org_id else None)


async def incr_user_usage(identity_key: str, tokens: int):
    """Increment user's daily token usage"""
    if not identity_key or tokens <= 0:
        return
    if redis_client:
        today = datetime.now(timezone.utc).date().isoformat()
        key = f"usage:{identity_key}:{today}"
        await safe_redis_op(redis_client.incrby(key, tokens))
        await safe_redis_op(redis_client.expire(key, 86400))  # 24 hours


async def get_user_usage(identity_key: str) -> int:
    """Get user's daily token usage"""
    if redis_client:
        today = datetime.now(timezone.utc).date().isoformat()
        key = f"usage:{identity_key}:{today}"
        val = await safe_redis_op(redis_client.get(key))
        return int(val) if val else 0
    return 0


# ============================================================
# DATA MODELS
# ============================================================

class Coordinate(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)

class BoundingBox(BaseModel):
    min_lat: float = Field(..., ge=-90, le=90)
    max_lat: float = Field(..., ge=-90, le=90)
    min_lon: float = Field(..., ge=-180, le=180)
    max_lon: float = Field(..., ge=-180, le=180)
    
    @property
    def area_hectares(self) -> float:
        lat_diff = abs(self.max_lat - self.min_lat)
        lon_diff = abs(self.max_lon - self.min_lon)
        width_km = lon_diff * 111 * np.cos(np.radians((self.max_lat + self.min_lat) / 2))
        height_km = lat_diff * 111
        return (width_km * height_km) * 100

    @property
    def geometry(self):
        return ee.Geometry.Rectangle([self.min_lon, self.min_lat, self.max_lon, self.max_lat])

class TimeRange(BaseModel):
    start_date: str
    end_date: str

class NDVIRequest(BaseModel):
    location: Coordinate
    date: str
    buffer_meters: int = 100
    satellite: str = "sentinel"

class ChangeDetectionRequest(BaseModel):
    region: BoundingBox
    time_range: TimeRange
    index: str = "ndvi"
    threshold: float = 0.15

class VegetationHealthRequest(BaseModel):
    location: Coordinate
    time_range: TimeRange
    metrics: List[str] = ["ndvi", "evi", "ndmi"]

class WildfireRiskRequest(BaseModel):
    region: BoundingBox
    date: str

class TimeSeriesForecastRequest(BaseModel):
    location: Coordinate
    time_range: TimeRange
    metric: str = "ndvi"
    forecast_days: int = 30

class AIAnalysisRequest(BaseModel):
    query: str
    context_data: Optional[Dict[str, Any]] = None
    stream: bool = False
    model: Optional[str] = None

class GeospatialResponse(BaseModel):
    success: bool
    data: Dict[str, Any]
    metadata: Dict[str, Any]
    timestamp: str

class ChangeDetectionResult(BaseModel):
    total_change_ha: float
    percent_change: float
    severity: str
    recommendations: List[str]


# ============================================================
# EARTH ENGINE CLIENT (With Circuit Breaker)
# ============================================================

class EarthEngineClient:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def initialize(self):
        if self._initialized:
            return
        try:
            credentials = settings.get_gee_credentials()
            if credentials:
                ee.Initialize(credentials, project=settings.gee_project_id)
            else:
                ee.Initialize(project=settings.gee_project_id)
            self._initialized = True
            logger.info("Google Earth Engine initialized")
        except Exception as e:
            logger.error(f"Earth Engine initialization failed: {e}")
            raise RuntimeError(f"Earth Engine initialization failed: {e}")

    @property
    def is_ready(self) -> bool:
        return self._initialized

    async def execute(self, operation: str, func, *args, **kwargs):
        """Execute Earth Engine operation with circuit breaker"""
        async def _execute():
            gee_requests_total.labels(operation=operation, status="pending").inc()
            try:
                result = await cpu_executor.run(func, *args, **kwargs)
                gee_requests_total.labels(operation=operation, status="success").inc()
                return result
            except Exception as e:
                gee_requests_total.labels(operation=operation, status="error").inc()
                raise
        
        return await gee_circuit_breaker.call(_execute)

    def get_sentinel_collection(self, start_date: str, end_date: str):
        return (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                .filterDate(start_date, end_date)
                .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20)))

    def calculate_ndvi(self, image: ee.Image):
        nir = image.select('B8')
        red = image.select('B4')
        return nir.subtract(red).divide(nir.add(red)).rename('NDVI')

    def calculate_evi(self, image: ee.Image):
        nir = image.select('B8')
        red = image.select('B4')
        blue = image.select('B2')
        evi = nir.subtract(red).multiply(2.5).divide(nir.add(red.multiply(6)).subtract(blue.multiply(7.5)).add(1))
        return evi.rename('EVI')

    def calculate_ndwi(self, image: ee.Image):
        green = image.select('B3')
        nir = image.select('B8')
        return green.subtract(nir).divide(green.add(nir)).rename('NDWI')

    def calculate_ndmi(self, image: ee.Image):
        nir = image.select('B8')
        swir = image.select('B11')
        return nir.subtract(swir).divide(nir.add(swir)).rename('NDMI')


gee_client = EarthEngineClient()


# ============================================================
# GEMINI AI CLIENT (With Circuit Breaker)
# ============================================================

class GeminiAIClient:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def initialize(self):
        if self._initialized:
            return
        if not GOOGLE_AI_AVAILABLE:
            logger.warning("Google Generative AI library not available")
            return
        try:
            from google import genai
            self.client = genai.Client(api_key=settings.gemini_api_key)
            self._initialized = True
            logger.info(f"Gemini AI initialized with model: {settings.gemini_model}")
        except Exception as e:
            logger.error(f"Gemini AI initialization failed: {e}")
            raise RuntimeError(f"Gemini AI initialization failed: {e}")

    @property
    def is_ready(self) -> bool:
        return self._initialized and GOOGLE_AI_AVAILABLE

    async def generate(self, query: str, geospatial_data: Optional[Dict] = None, stream: bool = False):
        """Generate environmental analysis with circuit breaker"""
        if not self.is_ready:
            raise HTTPException(503, "Gemini AI service not available")
        
        prompt = self._build_environmental_prompt(query, geospatial_data)
        
        async def _generate():
            start = time.time()
            try:
                from google.genai.types import GenerateContentConfig
                if stream:
                    return await self._stream_response(prompt)
                response = await cpu_executor.run(
                    self.client.models.generate_content,
                    model=settings.gemini_model,
                    contents=prompt,
                    config=GenerateContentConfig(
                        temperature=settings.gemini_temperature,
                        max_output_tokens=settings.gemini_max_output_tokens,
                        top_p=settings.gemini_top_p,
                        top_k=settings.gemini_top_k
                    )
                )
                duration = time.time() - start
                gemini_request_duration.labels(model=settings.gemini_model).observe(duration)
                gemini_requests_total.labels(model=settings.gemini_model, operation="generate", status="success").inc()
                return getattr(response, 'text', str(response))
            except Exception as e:
                gemini_requests_total.labels(model=settings.gemini_model, operation="generate", status="error").inc()
                raise
        
        return await gemini_circuit_breaker.call(_generate)

    def _build_environmental_prompt(self, query: str, geospatial_data: Optional[Dict] = None) -> str:
        system_prompt = (
            "You are Arybit Geospatial Intelligence, an expert environmental monitoring AI.\n"
            "Provide accurate, data-driven analysis using satellite imagery. Be concise and actionable.\n"
            "Include specific recommendations based on the data provided.\n"
        )
        prompt = system_prompt + "\n"
        if geospatial_data:
            prompt += f"## Geospatial Data Context:\n{json.dumps(geospatial_data, indent=2)}\n\n"
        prompt += f"## User Query:\n{query}\n\n"
        prompt += "## Response Requirements:\n"
        prompt += "1. Assess the current situation\n"
        prompt += "2. Identify trends or changes\n"
        prompt += "3. Provide actionable recommendations\n"
        prompt += "4. Note any data limitations or confidence levels\n"
        return prompt

    async def _stream_response(self, prompt: str):
        from google.genai.types import GenerateContentConfig
        response = self.client.models.generate_content_stream(
            model=settings.gemini_model,
            contents=prompt,
            config=GenerateContentConfig(
                temperature=settings.gemini_temperature,
                max_output_tokens=settings.gemini_max_output_tokens,
                top_p=settings.gemini_top_p,
                top_k=settings.gemini_top_k
            )
        )
        for chunk in response:
            if getattr(chunk, 'text', None):
                yield chunk.text
        gemini_requests_total.labels(model=settings.gemini_model, operation="stream", status="success").inc()


gemini_client = GeminiAIClient()


# ============================================================
# OLLAMA CLIENT FOR EMBEDDINGS (With Circuit Breaker)

class OllamaClient:
    _instance = None
    _client: Optional[httpx.AsyncClient] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=settings.ollama_host,
                timeout=httpx.Timeout(settings.ollama_timeout, connect=10.0, read=settings.ollama_timeout),
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=50)
            )
        return self._client

    async def create_embedding(self, text: str) -> Optional[List[float]]:
        if not text:
            return None

        cache_key = hashlib.md5(text.encode()).hexdigest()
        cached = embedding_cache.get(cache_key)
        if cached:
            cache_hits_total.labels(cache_type="embedding").inc()
            return cached

        cache_misses_total.labels(cache_type="embedding").inc()

        async def _embed():
            client = await self.get_client()
            resp = await client.post(
                "/api/embeddings",
                json={"model": settings.embedding_model, "prompt": text[:8000]},
                timeout=httpx.Timeout(45.0)
            )
            resp.raise_for_status()
            return resp.json().get("embedding")

        try:
            embedding = await ollama_circuit_breaker.call(_embed)
            if embedding:
                embedding_cache.set(cache_key, embedding)
            return embedding
        except Exception as e:
            logger.warning(f"Embedding failed: {e}")
            if SENTENCE_TRANSFORMERS_AVAILABLE:
                try:
                    from sentence_transformers import SentenceTransformer
                    model = SentenceTransformer('all-MiniLM-L6-v2')
                    embedding = await cpu_executor.run(model.encode, text)
                    return embedding.tolist()
                except Exception:
                    pass
            return None

    async def create_batch_embeddings(self, texts: List[str]) -> List[Optional[List[float]]]:
        if not texts:
            return []

        results = [None] * len(texts)
        batch_size = settings.embedding_batch_size

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_results = await asyncio.gather(*[self.create_embedding(t) for t in batch])
            for j, emb in enumerate(batch_results):
                results[i + j] = emb

        return results

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


ollama_client = OllamaClient()


# ============================================================
# DOCUMENT INTELLIGENCE SERVICE

class DocumentIntelligenceService:
    @staticmethod
    async def extract_text(file_bytes: bytes, filename: str, content_type: str, request_id: str) -> str:
        file_ext = os.path.splitext(filename)[1].lower()

        if file_ext == '.pdf' or 'application/pdf' in content_type:
            if FITZ_AVAILABLE:
                return await DocumentIntelligenceService._extract_pdf(file_bytes, request_id)
            return "[PDF extraction not available - missing PyMuPDF]"

        elif file_ext == '.docx' and DOCX_AVAILABLE:
            return await DocumentIntelligenceService._extract_docx(file_bytes, request_id)

        elif file_ext in ['.png', '.jpg', '.jpeg', '.gif', '.tiff'] and settings.ocr_enabled:
            return await DocumentIntelligenceService._ocr_image(file_bytes, request_id)

        elif file_ext in ['.txt', '.csv', '.json', '.xml', '.yaml', '.md', '.py', '.js']:
            try:
                return file_bytes.decode('utf-8', errors='ignore')
            except Exception:
                return file_bytes.decode('latin-1', errors='ignore')

        elif file_ext in ['.mp3', '.wav', '.ogg', '.m4a']:
            return f"[Audio file: {filename}] - Use transcription endpoint for processing"

        elif file_ext in ['.mp4', '.avi', '.mov', '.mkv']:
            return f"[Video file: {filename}] - Use video analysis endpoint"

        else:
            return f"[Unsupported file: {filename}]"

    @staticmethod
    async def _extract_pdf(file_bytes: bytes, request_id: str) -> str:
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            text_pages = [page.get_text("text") for page in doc]
            doc.close()
            full_text = "\n".join(text_pages)

            if len(full_text.strip()) < 500 and settings.ocr_enabled and TESSERACT_AVAILABLE:
                RequestContextLogger.info("Low text yield in PDF, attempting OCR", request_id=request_id)
                return await DocumentIntelligenceService._ocr_pdf(file_bytes, request_id)

            return full_text
        except Exception as e:
            RequestContextLogger.error(f"PDF extraction failed: {e}", request_id=request_id)
            return ""

    @staticmethod
    async def _extract_docx(file_bytes: bytes, request_id: str) -> str:
        try:
            doc = Document(io.BytesIO(file_bytes))
            return "\n".join([para.text for para in doc.paragraphs])
        except Exception as e:
            RequestContextLogger.error(f"DOCX extraction failed: {e}", request_id=request_id)
            return ""

    @staticmethod
    async def _ocr_pdf(file_bytes: bytes, request_id: str) -> str:
        if not TESSERACT_AVAILABLE:
            return ""

        try:
            from pdf2image import convert_from_bytes
            images = await cpu_executor.run(convert_from_bytes, file_bytes, dpi=200)
            ocr_texts = []
            for i, image in enumerate(images):
                text = await cpu_executor.run(pytesseract.image_to_string, image, lang='eng')
                ocr_texts.append(f"--- Page {i+1} ---\n{text}")
            return "\n".join(ocr_texts)
        except Exception as e:
            RequestContextLogger.error(f"PDF OCR failed: {e}", request_id=request_id)
            return ""

    @staticmethod
    async def _ocr_image(file_bytes: bytes, request_id: str) -> str:
        if not TESSERACT_AVAILABLE or not PIL_AVAILABLE:
            return "[OCR not available]"

        try:
            image = Image.open(io.BytesIO(file_bytes))
            text = await cpu_executor.run(pytesseract.image_to_string, image, lang='eng')
            return text.strip() or "[No text detected in image]"
        except Exception as e:
            RequestContextLogger.error(f"Image OCR failed: {e}", request_id=request_id)
            return "[OCR processing failed]"

    @staticmethod
    def chunk_text(text: str) -> List[str]:
        if not text:
            return []

        if len(text) > 50000:
            return DocumentIntelligenceService._simple_chunk(text)

        if len(text) < 2000:
            return DocumentIntelligenceService._legal_chunk(text)

        if SENTENCE_TRANSFORMERS_AVAILABLE:
            return DocumentIntelligenceService._semantic_chunk(text)

        return DocumentIntelligenceService._simple_chunk(text)

    @staticmethod
    def _legal_chunk(text: str) -> List[str]:
        chunk_size = settings.legal_chunk_size
        chunk_overlap = settings.legal_chunk_overlap

        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)

        chunks = []
        start = 0

        while start < len(text):
            end = start + chunk_size
            if end >= len(text):
                chunks.append(text[start:].strip())
                break

            patterns = [
                r'(?i)\b(Article|Section|Clause|Schedule)\s+\d+',
                r'(?i)\b\d+\.\s+',
                r'\n\s*\n',
                r'[.!?]\s+(?=[A-Z])',
            ]

            best_break = end
            for pattern in patterns:
                matches = list(re.finditer(pattern, text[start:end + 100]))
                if matches:
                    candidate = matches[-1].end() + start
                    if candidate > start + 250:
                        best_break = min(candidate, end + 80)
                        break

            chunk = text[start:best_break].strip()
            if chunk and len(chunk) >= 50:
                chunks.append(chunk)

            start = best_break - chunk_overlap
            if start < 0:
                start = 0

        return chunks

    @staticmethod
    def _semantic_chunk(text: str, threshold: float = 0.45) -> List[str]:
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer('all-MiniLM-L6-v2')
            sentences = re.split(r'(?<=[.!?])\s+', text)

            if len(sentences) < 2:
                return [text]

            embeddings = model.encode(sentences)
            chunks = []
            current_chunk = [sentences[0]]

            for i in range(len(sentences) - 1):
                sim = np.dot(embeddings[i], embeddings[i+1]) / (
                    np.linalg.norm(embeddings[i]) * np.linalg.norm(embeddings[i+1])
                )
                if sim < threshold or len(" ".join(current_chunk)) > 1200:
                    chunks.append(" ".join(current_chunk))
                    current_chunk = []
                current_chunk.append(sentences[i+1])

            if current_chunk:
                chunks.append(" ".join(current_chunk))

            return chunks
        except Exception as e:
            logger.warning(f"Semantic chunking failed: {e}")
            return DocumentIntelligenceService._simple_chunk(text)

    @staticmethod
    def _simple_chunk(text: str, chunk_size: int = 1500, overlap: int = 200) -> List[str]:
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + chunk_size, len(text))
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text):
                break
            start = end - overlap
        return chunks


doc_service = DocumentIntelligenceService()


# ============================================================
# GEOSPATIAL ANALYSIS SERVICE (CPU-bound operations in executor)
# ============================================================

class GeospatialAnalysisService:
    def __init__(self):
        self.cache = BoundedLRUCache(maxsize=1000, ttl_seconds=settings.cache_ttl_seconds)

    async def get_ndvi(self, request: NDVIRequest) -> Dict[str, Any]:
        cache_key = f"ndvi:{request.location.lat}:{request.location.lon}:{request.date}"
        cached = self.cache.get(cache_key)
        if cached:
            cache_hits_total.labels(cache_type="ndvi").inc()
            return cached
        cache_misses_total.labels(cache_type="ndvi").inc()
        
        gee_client.initialize()
        point = ee.Geometry.Point([request.location.lon, request.location.lat])
        collection = gee_client.get_sentinel_collection(request.date, request.date)
        image = collection.first()
        
        if not image:
            raise HTTPException(404, f"No imagery found for date {request.date}")
        
        ndvi = gee_client.calculate_ndvi(image)
        
        def _extract_value():
            return ndvi.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=point.buffer(request.buffer_meters),
                scale=10,
                bestEffort=True
            ).get('NDVI').getInfo()
        
        value = await gee_client.execute("get_ndvi_value", _extract_value)
        
        if value is None:
            classification = "unknown"
        elif value < settings.ndvi_water_threshold:
            classification = "water"
        elif value < settings.ndvi_sparse_threshold:
            classification = "barren"
        elif value < settings.ndvi_moderate_threshold:
            classification = "sparse_vegetation"
        elif value < settings.ndvi_dense_threshold:
            classification = "moderate_vegetation"
        else:
            classification = "dense_vegetation"
        
        result = {
            "ndvi": round(float(value), 4) if value else None,
            "classification": classification,
            "location": request.location.dict(),
            "date": request.date,
            "satellite": request.satellite
        }
        
        self.cache.set(cache_key, result)
        return result

    async def detect_change(self, request: ChangeDetectionRequest) -> ChangeDetectionResult:
        gee_client.initialize()
        region = request.region.geometry
        
        start = datetime.fromisoformat(request.time_range.start_date)
        end = datetime.fromisoformat(request.time_range.end_date)
        mid = start + (end - start) / 2
        mid_str = mid.strftime("%Y-%m-%d")
        
        before_collection = gee_client.get_sentinel_collection(request.time_range.start_date, mid_str)
        after_collection = gee_client.get_sentinel_collection(mid_str, request.time_range.end_date)
        
        before_image = before_collection.median()
        after_image = after_collection.median()
        
        if request.index == "ndvi":
            before_idx = gee_client.calculate_ndvi(before_image)
            after_idx = gee_client.calculate_ndvi(after_image)
        elif request.index == "ndwi":
            before_idx = gee_client.calculate_ndwi(before_image)
            after_idx = gee_client.calculate_ndwi(after_image)
        else:
            before_idx = gee_client.calculate_ndvi(before_image)
            after_idx = gee_client.calculate_ndvi(after_image)
        
        difference = after_idx.subtract(before_idx).abs()
        change_mask = difference.gt(request.threshold)
        
        def _calculate_change():
            stats = change_mask.reduceRegion(
                reducer=ee.Reducer.sum(),
                geometry=region,
                scale=10,
                bestEffort=True,
                maxPixels=1e9
            )
            return stats.get('sum').getInfo() or 0
        
        total_pixels = await gee_client.execute("detect_change", _calculate_change)
        total_change_ha = total_pixels * 0.01
        percent_change = (total_change_ha / request.region.area_hectares * 100) if request.region.area_hectares > 0 else 0
        
        if percent_change < 5:
            severity = "low"
        elif percent_change < 15:
            severity = "medium"
        elif percent_change < 30:
            severity = "high"
        else:
            severity = "critical"
        
        recommendations = []
        if request.index == "ndvi":
            if severity == "critical":
                recommendations.append("Severe vegetation loss detected - immediate intervention required")
                recommendations.append("Schedule field verification and assess erosion risk")
            elif severity == "high":
                recommendations.append("Significant vegetation decline - investigate causes (drought, fire, deforestation)")
            else:
                recommendations.append("Continue monitoring - implement bi-weekly NDVI tracking")
        
        if request.index == "ndwi" and percent_change > 20:
            recommendations.append("Water body change detected - conduct hydrological assessment")
        
        return ChangeDetectionResult(
            total_change_ha=round(total_change_ha, 2),
            percent_change=round(percent_change, 1),
            severity=severity,
            recommendations=recommendations or ["Continue regular monitoring"]
        )

    async def analyze_vegetation_health(self, request: VegetationHealthRequest) -> Dict[str, Any]:
        gee_client.initialize()
        point = ee.Geometry.Point([request.location.lon, request.location.lat])
        results = {}
        
        for metric in request.metrics:
            values = await self._get_time_series(point, metric, request.time_range)
            results[metric] = values
        
        ndvi_values = [v["value"] for v in results.get("ndvi", [])]
        if ndvi_values:
            avg_ndvi = sum(ndvi_values) / len(ndvi_values)
            if avg_ndvi > 0.6:
                health = "excellent"
            elif avg_ndvi > 0.4:
                health = "good"
            elif avg_ndvi > 0.2:
                health = "fair"
            else:
                health = "poor"
        else:
            health = "unknown"
        
        trend = self._calculate_trend(ndvi_values) if ndvi_values else {"direction": "unknown", "percent": 0}
        
        recommendations = []
        if health == "poor":
            recommendations.append("Immediate restoration intervention recommended")
            recommendations.append("Conduct soil moisture assessment and consider irrigation")
        elif health == "fair" and trend.get("direction") == "decreasing":
            recommendations.append("Monitor weekly - vegetation declining")
            recommendations.append("Investigate potential stressors (pests, disease, water stress)")
        elif health == "good":
            recommendations.append("Vegetation healthy - continue standard monitoring")
        
        if trend.get("direction") == "decreasing" and trend.get("percent", 0) > 15:
            recommendations.append("Significant negative trend detected - investigate causes")
        
        return {
            "location": request.location.dict(),
            "time_range": request.time_range.dict(),
            "metrics": results,
            "overall_health": health,
            "trend": trend,
            "recommendations": recommendations or ["Continue regular monitoring"]
        }

    async def _get_time_series(self, point: ee.Geometry, metric: str, time_range: TimeRange) -> List[Dict]:
        collection = gee_client.get_sentinel_collection(time_range.start_date, time_range.end_date)
        metric_funcs = {
            "ndvi": gee_client.calculate_ndvi,
            "evi": gee_client.calculate_evi,
            "ndmi": gee_client.calculate_ndmi
        }
        compute_func = metric_funcs.get(metric, gee_client.calculate_ndvi)
        
        def _extract_series():
            values = []
            image_list = collection.toList(collection.size())
            size = min(collection.size().getInfo(), 50)
            for i in range(size):
                image = ee.Image(image_list.get(i))
                date = image.date().format().getInfo()
                idx = compute_func(image)
                value = idx.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=point.buffer(100),
                    scale=10,
                    bestEffort=True
                ).get(metric.upper()).getInfo()
                if value is not None:
                    values.append({"date": date[:10], "value": round(float(value), 4)})
            return values
        
        return await gee_client.execute(f"get_{metric}_timeseries", _extract_series)

    def _calculate_trend(self, values: List[float]) -> Dict[str, Any]:
        if len(values) < 3:
            return {"direction": "insufficient_data", "percent": 0}
        
        x = list(range(len(values)))
        n = len(x)
        x_mean = sum(x) / n
        y_mean = sum(values) / n
        
        numerator = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
        slope = numerator / denominator if denominator != 0 else 0
        
        if slope > 0.01:
            direction = "increasing"
        elif slope < -0.01:
            direction = "decreasing"
        else:
            direction = "stable"
        
        percent_change = (slope * n / y_mean * 100) if y_mean > 0 else 0
        return {"direction": direction, "percent": round(abs(percent_change), 1)}

    async def assess_wildfire_risk(self, request: WildfireRiskRequest) -> Dict[str, Any]:
        gee_client.initialize()
        region = request.region.geometry
        collection = gee_client.get_sentinel_collection(request.date, request.date)
        image = collection.first()
        
        if not image:
            raise HTTPException(404, "No imagery available for risk assessment")
        
        ndmi = gee_client.calculate_ndmi(image)
        
        def _get_moisture():
            return ndmi.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=region,
                scale=500,
                bestEffort=True
            ).get('NDMI').getInfo()
        
        moisture = await gee_client.execute("get_moisture", _get_moisture)
        
        if moisture is None:
            risk_score = 50
            risk_level = "unknown"
        elif moisture < -0.2:
            risk_score = 90
            risk_level = "critical"
        elif moisture < -0.1:
            risk_score = 75
            risk_level = "high"
        elif moisture < 0:
            risk_score = 55
            risk_level = "medium"
        elif moisture < 0.1:
            risk_score = 35
            risk_level = "low"
        else:
            risk_score = 15
            risk_level = "minimal"
        
        recommendations = []
        if risk_level in ("high", "critical"):
            recommendations.append("Fire weather watch - restrict outdoor burning")
            recommendations.append("Activate monitoring protocols and alert response teams")
            recommendations.append("Pre-position fire suppression resources")
        elif risk_level == "medium":
            recommendations.append("Elevated risk - monitor conditions closely")
            recommendations.append("Review fire response plans")
        else:
            recommendations.append("Normal conditions - maintain standard monitoring")
        
        return {
            "risk_level": risk_level,
            "risk_score": risk_score,
            "moisture_index": round(float(moisture), 4) if moisture else None,
            "date": request.date,
            "recommendations": recommendations
        }

    async def forecast_time_series(self, request: TimeSeriesForecastRequest) -> Dict[str, Any]:
        gee_client.initialize()
        point = ee.Geometry.Point([request.location.lon, request.location.lat])
        
        historical = await self._get_time_series(point, request.metric, request.time_range)
        
        if len(historical) < 3:
            raise HTTPException(400, "Insufficient historical data for forecasting (minimum 3 data points)")
        
        values = [h["value"] for h in historical]
        trend = self._calculate_trend(values)
        
        forecast = []
        if len(values) >= 3:
            x = list(range(len(values)))
            n = len(x)
            x_mean = sum(x) / n
            y_mean = sum(values) / n
            
            numerator = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
            denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
            slope = numerator / denominator if denominator != 0 else 0
            
            last_date = datetime.fromisoformat(historical[-1]["date"])
            for days in range(7, request.forecast_days + 1, 7):
                forecast_date = last_date + timedelta(days=days)
                forecast_value = max(0, min(1, y_mean + slope * (n + days // 7)))
                forecast.append({
                    "date": forecast_date.strftime("%Y-%m-%d"),
                    "value": round(forecast_value, 4),
                    "quality_flag": "forecast"
                })
        
        return {
            "metric": request.metric,
            "historical": historical,
            "forecast": forecast,
            "trend": trend,
            "confidence": "high" if len(historical) >= 15 else "medium" if len(historical) >= 8 else "low"
        }


geo_service = GeospatialAnalysisService()


# ============================================================
# REDIS CLIENT (With Connection Management)
# ============================================================

redis_client = None
redis_health_check_task = None


async def init_redis():
    """Initialize Redis connection with retry and health monitoring"""
    global redis_client, redis_health_check_task
    
    try:
        if settings.redis_url:
            redis_url = settings.redis_url
        else:
            redis_url = f"redis://{settings.redis_host}:{settings.redis_port}"
            if settings.redis_password:
                redis_url = f"redis://:{settings.redis_password}@{settings.redis_host}:{settings.redis_port}"
        
        client = aioredis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=settings.redis_connect_timeout,
            socket_timeout=settings.redis_socket_timeout,
            max_connections=settings.redis_max_connections,
            retry_on_timeout=True,
            health_check_interval=30
        )
        
        await client.ping()
        redis_client = client
        logger.info("Redis connected successfully")
        
        # Start health check task
        if redis_health_check_task is None:
            redis_health_check_task = asyncio.create_task(redis_health_check_loop())
        
        return client
        
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}. Running in memory-only mode.")
        redis_client = None
        return None


async def redis_health_check_loop():
    """Background task to monitor Redis health and reconnect if needed"""
    global redis_client
    
    while True:
        await asyncio.sleep(30)
        try:
            if redis_client:
                await redis_client.ping()
            elif redis_client is None:
                # Attempt reconnection
                await init_redis()
        except Exception as e:
            logger.warning(f"Redis health check failed: {e}")
            redis_client = None


async def safe_redis_op(coro, default=None):
    """Safely execute Redis operation with circuit breaker"""
    if not redis_client:
        return default
    
    async def _op():
        return await coro
    
    try:
        return await redis_circuit_breaker.call(_op)
    except Exception as e:
        logger.debug(f"Redis operation failed: {e}")
        return default


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title=settings.app_name,
    description="AI-Enhanced Geospatial Intelligence for Environmental Monitoring with Google Gemini AI",
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc"
)

# Add middleware (order matters - auth last)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://arybit.co.ke",
        "https://api.arybit.co.ke",
        "https://account.arybit.co.ke",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=7200,
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Prometheus metrics
Instrumentator().instrument(app).expose(app)


@app.on_event("startup")
async def startup_event():
    """Initialize all services on startup"""
    logger.info(f"🚀 Starting {settings.app_name} v{settings.app_version}")
    logger.info(f"Environment: {settings.environment}")
    logger.info(f"Auth Mode: {settings.auth_mode}")
    
    # Initialize Earth Engine
    try:
        gee_client.initialize()
        logger.info("✅ Google Earth Engine ready")
    except Exception as e:
        logger.error(f"❌ Earth Engine initialization failed: {e}")
    
    # Initialize Gemini AI
    try:
        gemini_client.initialize()
        logger.info(f"✅ Gemini AI ready - Model: {settings.gemini_model}")
    except Exception as e:
        logger.error(f"❌ Gemini AI initialization failed: {e}")
    
    # Initialize Redis
    await init_redis()
    
    # Start background cleanup tasks
    asyncio.create_task(cleanup_expired_cache())
    asyncio.create_task(cleanup_rate_limits())
    
    # Initialize Google Embeddings
    try:
        google_embeddings.initialize(model=settings.GOOGLE_EMBEDDING_MODEL)
        logger.info(f"✅ Google Embeddings ready - Model: {settings.GOOGLE_EMBEDDING_MODEL}, Dimension: {google_embeddings.get_embedding_dimension()}")
    except Exception as e:
        logger.error(f"❌ Google Embeddings initialization failed: {e}")

    logger.info(f"✅ {settings.app_name} ready")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean shutdown of all services"""
    logger.info("🛑 Shutting down...")
    
    # Close Google Embeddings if initialized
    try:
        await google_embeddings.close()
    except Exception:
        pass
    
    # Shutdown thread pool
    cpu_executor.shutdown()
    
    # Close Redis connection
    if redis_client:
        await redis_client.close()
    
    logger.info("✅ Shutdown complete")


async def cleanup_expired_cache():
    """Background task to clean expired cache entries"""
    while True:
        await asyncio.sleep(300)  # Every 5 minutes
        try:
            # Clean auth cache
            auth_cache.clear_expired()
            
            # Clean rate limit stores
            now = time.time()
            for key, window in list(rate_limit_store.items()):
                window.clean_expired(now)
                if len(window) == 0:
                    del rate_limit_store[key]
            
            logger.debug("Cache cleanup completed")
        except Exception as e:
            logger.error(f"Cache cleanup error: {e}")


async def cleanup_rate_limits():
    """Background task to prune old rate limit windows"""
    while True:
        await asyncio.sleep(300)
        try:
            now = time.time()
            for key, window in list(rate_limit_store.items()):
                window.clean_expired(now)
                if len(window) == 0:
                    del rate_limit_store[key]
        except Exception as e:
            logger.error(f"Rate limit cleanup error: {e}")


# ============================================================
# HEALTH ENDPOINTS
# ============================================================

@app.get("/health", tags=["Health"])
async def health_check():
    """Comprehensive health check"""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version,
        "gee_ready": gee_client.is_ready,
        "gemini_ready": gemini_client.is_ready,
        "redis_ready": redis_client is not None,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/healthz", tags=["Health"])
async def healthz():
    """Liveness probe for orchestration"""
    return {"status": "ok"}


@app.get("/ready", tags=["Health"])
async def readiness():
    """Readiness probe"""
    if not gee_client.is_ready:
        return JSONResponse(status_code=503, content={"status": "not_ready", "reason": "Earth Engine not initialized"})
    return {"status": "ready"}


@app.get("/ping", tags=["Health"])
async def ping():
    """Simple ping for load balancers"""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


# ============================================================
# AUTHENTICATION ENDPOINTS
# ============================================================

@app.get("/auth/verify", tags=["Authentication"])
async def verify_token(user: dict = Depends(get_current_user)):
    """Verify current authentication token"""
    return {
        "authenticated": True,
        "user": {
            "user_id": user.get("user_id"),
            "username": user.get("username"),
            "role": user.get("role"),
            "kyc_status": user.get("kyc_status")
        }
    }


@app.get("/auth/profile", tags=["Authentication"])
async def get_profile(user: dict = Depends(get_current_user)):
    """Get full user profile"""
    return user


# ============================================================
# GEOSPATIAL ANALYSIS ENDPOINTS
# ============================================================

@app.post("/api/ndvi", tags=["Geospatial"])
async def get_ndvi_endpoint(request: NDVIRequest, user: dict = Depends(require_verified_user)):
    """Calculate NDVI (Normalized Difference Vegetation Index) for a location"""
    try:
        result = await geo_service.get_ndvi(request)
        return GeospatialResponse(
            success=True,
            data=result,
            metadata={"model": "NDVI", "user_id": user.get("user_id")},
            timestamp=datetime.now(timezone.utc).isoformat()
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"NDVI calculation failed: {e}")
        raise HTTPException(500, f"Analysis failed: {str(e)}")

@app.post("/api/change-detection", tags=["Geospatial"])
async def detect_change_endpoint(request: ChangeDetectionRequest, user: dict = Depends(require_verified_user)):
    """Detect environmental change between two time periods"""
    try:
        result = await geo_service.detect_change(request)
        return GeospatialResponse(
            success=True,
            data=result.dict(),
            metadata={"index": request.index, "user_id": user.get("user_id")},
            timestamp=datetime.now(timezone.utc).isoformat()
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Change detection failed: {e}")
        raise HTTPException(500, f"Change detection failed: {str(e)}")

@app.post("/api/vegetation-health", tags=["Environmental"])
async def analyze_vegetation_health_endpoint(request: VegetationHealthRequest, user: dict = Depends(require_verified_user)):
    """Comprehensive vegetation health analysis using multiple spectral indices"""
    try:
        result = await geo_service.analyze_vegetation_health(request)
        return GeospatialResponse(
            success=True,
            data=result,
            metadata={"metrics": request.metrics, "user_id": user.get("user_id")},
            timestamp=datetime.now(timezone.utc).isoformat()
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Vegetation health analysis failed: {e}")
        raise HTTPException(500, f"Analysis failed: {str(e)}")

@app.post("/api/wildfire-risk", tags=["Environmental"])
async def assess_wildfire_risk_endpoint(request: WildfireRiskRequest, user: dict = Depends(require_verified_user)):
    """Assess wildfire risk based on vegetation moisture index"""
    try:
        result = await geo_service.assess_wildfire_risk(request)
        return GeospatialResponse(
            success=True,
            data=result,
            metadata={"assessment_date": request.date, "user_id": user.get("user_id")},
            timestamp=datetime.now(timezone.utc).isoformat()
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Wildfire risk assessment failed: {e}")
        raise HTTPException(500, f"Risk assessment failed: {str(e)}")

@app.post("/api/forecast", tags=["Time Series"])
async def forecast_time_series_endpoint(request: TimeSeriesForecastRequest, user: dict = Depends(require_verified_user)):
    """Forecast environmental metrics using historical satellite data"""
    try:
        result = await geo_service.forecast_time_series(request)
        return GeospatialResponse(
            success=True,
            data=result,
            metadata={"forecast_days": request.forecast_days, "user_id": user.get("user_id")},
            timestamp=datetime.now(timezone.utc).isoformat()
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Forecast failed: {e}")
        raise HTTPException(500, f"Forecast failed: {str(e)}")


# ============================================================
# GEMINI AI ANALYSIS ENDPOINTS
# ============================================================

@app.post("/api/ai/analyze", tags=["AI Analysis"])
async def ai_environmental_analysis(request: AIAnalysisRequest, user: dict = Depends(require_verified_user)):
    """Get AI-powered environmental analysis using Google Gemini AI"""
    if not gemini_client.is_ready:
        raise HTTPException(503, "Gemini AI service not ready")
    
    # Apply grace limits for unverified users
    estimated_tokens = len(request.query) // 4
    await apply_grace_limits(request, user, estimated_tokens)
    
    try:
        response = await gemini_client.generate(
            query=request.query,
            geospatial_data=request.context_data,
            stream=request.stream
        )
        
        if request.stream:
            async def stream_generator():
                async for chunk in response:
                    yield f"data: {json.dumps({'token': chunk})}\n\n"
                yield "data: [DONE]\n\n"
            
            return StreamingResponse(
                stream_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no"
                }
            )
        
        return {
            "success": True,
            "analysis": response,
            "model": settings.gemini_model,
            "user_id": user.get("user_id"),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI analysis failed: {e}")
        raise HTTPException(500, f"AI analysis failed: {str(e)}")


# ============================================================
# DOCUMENT INTELLIGENCE ENDPOINTS

@app.post("/api/documents/analyze", tags=["Document Intelligence"])
async def analyze_document(
    request: Request,
    file: UploadFile = File(...),
    prompt: Optional[str] = Form(None),
    user: dict = Depends(require_verified_user)
):
    """Analyze document using AI with multi-format support"""
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))

    file_bytes = await file.read()
    if len(file_bytes) > settings.max_document_size_mb * 1024 * 1024:
        raise HTTPException(413, f"File too large. Max {settings.max_document_size_mb}MB")

    extracted_text = await doc_service.extract_text(
        file_bytes, file.filename or "document", file.content_type or "application/octet-stream", request_id
    )

    if not extracted_text or len(extracted_text.strip()) < 50:
        raise HTTPException(400, "Could not extract meaningful text from document")

    estimated_tokens = len(extracted_text) // 4 + 200
    await apply_grace_limits(request, user, estimated_tokens)

    analysis_prompt = prompt or "Analyze this document and provide a comprehensive summary with key insights:"
    full_prompt = f"Document Content:\n{extracted_text[:8000]}\n\n{analysis_prompt}"

    try:
        response = await gemini_client.generate(query=full_prompt, stream=False)
        if getattr(request.state, "kyc_grace_mode", False):
            identity_key = request_identity_key(request, user)
            await incr_user_usage(identity_key, estimated_tokens)

        return {
            "success": True,
            "analysis": response,
            "filename": file.filename,
            "extracted_length": len(extracted_text),
            "model": settings.gemini_model,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except Exception as e:
        logger.error(f"Document analysis failed: {e}")
        raise HTTPException(500, f"Analysis failed: {str(e)}")


@app.post("/api/documents/embeddings", tags=["Document Intelligence"])
async def create_document_embeddings(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(require_verified_user)
):
    """Create embeddings from document for similarity search"""
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))

    file_bytes = await file.read()
    if len(file_bytes) > settings.max_document_size_mb * 1024 * 1024:
        raise HTTPException(413, f"File too large. Max {settings.max_document_size_mb}MB")

    extracted_text = await doc_service.extract_text(
        file_bytes, file.filename or "document", file.content_type or "application/octet-stream", request_id
    )

    if not extracted_text:
        raise HTTPException(400, "Could not extract text from document")

    chunks = doc_service.chunk_text(extracted_text)
    if not chunks:
        raise HTTPException(400, "No valid chunks created from document")

    embeddings = await ollama_client.create_batch_embeddings(chunks)
    results = []
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        if embedding:
            results.append({
                "chunk_index": i,
                "text": chunk[:500],
                "text_length": len(chunk),
                "embedding_dimension": len(embedding),
                "embedding": embedding[:10]
            })

    return {
        "success": True,
        "filename": file.filename,
        "total_chunks": len(chunks),
        "successful_embeddings": len(results),
        "chunks": results[:20],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# ============================================================
# PUBLIC METADATA ENDPOINTS
# ============================================================

@app.get("/api/satellites", tags=["Metadata"])
async def list_satellites():
    """List available satellite data sources"""
    return {
        "satellites": [
            {
                "name": "sentinel-2",
                "provider": "ESA",
                "resolution": "10m",
                "bands": ["B2", "B3", "B4", "B8", "B11", "B12"],
                "revisit_days": 5,
                "applications": ["vegetation", "water", "land_cover"]
            },
            {
                "name": "landsat-8",
                "provider": "NASA/USGS",
                "resolution": "30m",
                "revisit_days": 16,
                "applications": ["land_use", "thermal", "change_detection"]
            },
            {
                "name": "modis",
                "provider": "NASA",
                "resolution": "250m",
                "revisit_days": 1,
                "applications": ["large_scale", "daily_monitoring", "fire_detection"]
            }
        ]
    }


@app.get("/api/indices", tags=["Metadata"])
async def list_indices():
    """List available spectral indices for analysis"""
    return {
        "indices": [
            {
                "name": "NDVI",
                "full_name": "Normalized Difference Vegetation Index",
                "formula": "(NIR - Red) / (NIR + Red)",
                "range": [-1, 1],
                "applications": ["vegetation_health", "crop_monitoring", "deforestation"]
            },
            {
                "name": "EVI",
                "full_name": "Enhanced Vegetation Index",
                "formula": "2.5 * (NIR - Red) / (NIR + 6*Red - 7.5*Blue + 1)",
                "range": [-1, 1],
                "applications": ["dense_vegetation", "atmospheric_correction"]
            },
            {
                "name": "NDWI",
                "full_name": "Normalized Difference Water Index",
                "formula": "(Green - NIR) / (Green + NIR)",
                "range": [-1, 1],
                "applications": ["water_body_detection", "flood_mapping"]
            },
            {
                "name": "NDMI",
                "full_name": "Normalized Difference Moisture Index",
                "formula": "(NIR - SWIR) / (NIR + SWIR)",
                "range": [-1, 1],
                "applications": ["wildfire_risk", "drought_monitoring"]
            },
            {
                "name": "MSAVI2",
                "full_name": "Modified Soil Adjusted Vegetation Index 2",
                "formula": "(2*NIR + 1 - sqrt((2*NIR+1)^2 - 8*(NIR - Red))) / 2",
                "range": [0, 1],
                "applications": ["arid_regions", "soil_background_correction"]
            }
        ]
    }


@app.get("/api/usage", tags=["User"])
async def get_usage(user: dict = Depends(get_current_user)):
    """Get current user's API usage statistics"""
    identity_key = build_identity_key(user.get("user_id"), None, None)
    usage = await get_user_usage(identity_key)
    
    return {
        "user_id": user.get("user_id"),
        "kyc_status": user.get("kyc_status"),
        "subscription": user.get("subscription", {}),
        "daily_tokens_used": usage,
        "daily_tokens_limit": settings.grace_max_tokens if not user.get("is_verified") else "unlimited",
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


# ============================================================
# MAIN ENTRY POINT
# ============================================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    
    logger.info(f"Starting {settings.app_name} on {host}:{port}")
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=False,
        log_level=settings.log_level.lower(),
        workers=int(os.getenv("WORKERS", "4"))
    )
