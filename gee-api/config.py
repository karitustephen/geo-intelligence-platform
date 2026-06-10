import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, BaseSettings, Field, field_validator, model_validator
from pydantic_settings import SettingsConfigDict


def _parse_csv_list(value: Any) -> List[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


class EarthEngineConfig(BaseModel):
    service_account: str = ""
    private_key: str = ""
    project_id: str = ""


class GeminiConfig(BaseModel):
    api_key: str = ""
    model: str = "gemini-2.0-flash-exp"
    vision_model: str = "gemini-2.0-flash-exp"
    temperature: float = 0.7
    max_output_tokens: int = 8192
    top_p: float = 0.95
    top_k: int = 40


class AuthConfig(BaseModel):
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_secret_1: Optional[str] = None
    jwt_secret_2: Optional[str] = None
    auth_api_base: str = "https://api.arybit.co.ke"
    auth_internal_base: str = "http://auth-service:8001"
    internal_secret: str = ""
    internal_service_name: str = "arybit-geo-intelligence"
    api_keys: List[str] = Field(default_factory=list)
    auth_mode: str = "remote"

    @field_validator("api_keys", mode="before")
    def _parse_api_keys(cls, value: Any) -> List[str]:
        return _parse_csv_list(value)


class RedisConfig(BaseModel):
    host: str = "localhost"
    port: int = 6379
    password: Optional[str] = None
    url: Optional[str] = None
    max_connections: int = 50
    socket_timeout: int = 10
    connect_timeout: int = 5


class RateLimitConfig(BaseModel):
    per_minute: int = 60
    anonymous_per_minute: int = 30
    max_entries: int = 50000


class GeospatialConfig(BaseModel):
    ndvi_water_threshold: float = 0.0
    ndvi_sparse_threshold: float = 0.2
    ndvi_moderate_threshold: float = 0.4
    ndvi_dense_threshold: float = 0.6
    change_detection_threshold: float = 0.15
    max_area_hectares: float = 10000.0
    max_time_range_days: int = 365


class StorageConfig(BaseModel):
    bucket_name: str = "geo-intelligence-data"
    bigquery_dataset: str = "geospatial_analytics"
    gcp_project_id: str = ""


class Settings(BaseSettings):
    """Unified configuration with environment variable support"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
    )

    # ===== API Configuration =====
    APP_NAME: str = "Arybit Geospatial Intelligence"
    APP_VERSION: str = "2.0.0"
    ENVIRONMENT: str = "production"
    DEBUG: bool = False
    PORT: int = 8000
    HOST: str = "0.0.0.0"
    WORKERS: int = 4
    LOG_LEVEL: str = "INFO"

    # ===== Authentication =====
    AUTH_MODE: str = "remote"
    JWT_SECRET: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_SECRET_1: Optional[str] = None
    JWT_SECRET_2: Optional[str] = None
    AUTH_API_BASE: str = "https://api.arybit.co.ke"
    AUTH_INTERNAL_BASE: str = "http://auth-service:8001"
    AI_GATEWAY_INTERNAL_SECRET: str = ""
    INTERNAL_SERVICE_NAME: str = "arybit-geo-intelligence"
    API_KEYS: List[str] = Field(default_factory=list)

    # ===== Google Earth Engine =====
    GEE_SERVICE_ACCOUNT: str = ""
    GEE_PRIVATE_KEY: str = ""
    GEE_PROJECT_ID: str = ""

    # ===== Google Gemini AI =====
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.0-flash-exp"
    GEMINI_VISION_MODEL: str = "gemini-2.0-flash-exp"
    GEMINI_TEMPERATURE: float = 0.7
    GEMINI_MAX_OUTPUT_TOKENS: int = 8192
    GEMINI_TOP_P: float = 0.95
    GEMINI_TOP_K: int = 40

    # ===== Google Embeddings =====
    GOOGLE_EMBEDDING_MODEL: str = "text-embedding-004"
    GOOGLE_EMBEDDING_TASK_TYPE: str = "RETRIEVAL_DOCUMENT"
    GOOGLE_EMBEDDING_BATCH_SIZE: int = 10
    GOOGLE_EMBEDDING_MAX_RETRIES: int = 3
    GOOGLE_EMBEDDING_TIMEOUT: int = 30

    # ===== Redis =====
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = None
    REDIS_URL: Optional[str] = None
    REDIS_MAX_CONNECTIONS: int = 50
    REDIS_SOCKET_TIMEOUT: int = 10
    REDIS_CONNECT_TIMEOUT: int = 5

    # ===== Rate Limiting =====
    RATE_LIMIT_PER_MINUTE: int = 60
    RATE_LIMIT_ANONYMOUS_PER_MINUTE: int = 30
    GLOBAL_MAX_CONCURRENT_REQUESTS: int = 50

    # ===== Geospatial Analysis =====
    NDVI_WATER_THRESHOLD: float = 0.0
    NDVI_SPARSE_THRESHOLD: float = 0.2
    NDVI_MODERATE_THRESHOLD: float = 0.4
    NDVI_DENSE_THRESHOLD: float = 0.6
    CHANGE_DETECTION_THRESHOLD: float = 0.15
    MAX_AREA_HECTARES: float = 10000.0

    # ===== Document Intelligence =====
    MAX_DOCUMENT_SIZE_MB: int = 50
    OCR_ENABLED: bool = True
    LEGAL_CHUNK_SIZE: int = 2048
    LEGAL_CHUNK_OVERLAP: int = 256
    SEMANTIC_CHUNK_THRESHOLD: float = 0.45

    # ===== Grace Mode (Unverified Users) =====
    GRACE_MAX_TOKENS: int = 4096
    GRACE_MAX_PROMPT_CHARS: int = 16000
    GRACE_MAX_DOCUMENTS_PER_DAY: int = 5
    GRACE_MAX_DOC_SIZE_MB: int = 10
    GRACE_ALLOWED_MODELS: List[str] = Field(default_factory=lambda: ["gemini-2.0-flash-exp"])

    # ===== Cache =====
    CACHE_TTL_SECONDS: int = 3600
    AUTH_CACHE_TTL: int = 60
    MAX_AUTH_CACHE_SIZE: int = 10000
    EMBEDDING_CACHE_MAX_SIZE: int = 10000
    EMBEDDING_CACHE_TTL: int = 86400

    # ===== Threading =====
    CPU_EXECUTOR_THREADS: int = 4

    # ===== Circuit Breaker =====
    CIRCUIT_BREAKER_THRESHOLD: int = 5
    CIRCUIT_BREAKER_TIMEOUT: int = 30

    # ===== Security =====
    TRUSTED_PROXIES: str = "127.0.0.1,::1,10.0.0.0/8"
    BLOCKED_IPS: List[str] = Field(default_factory=list)

    # ===== CORS =====
    ALLOWED_ORIGINS: List[str] = Field(default_factory=lambda: [
        "https://arybit.co.ke",
        "https://api.arybit.co.ke",
        "https://account.arybit.co.ke",
        "http://localhost:3000",
    ])

    # Nested configuration helpers
    earth_engine: EarthEngineConfig = Field(default_factory=EarthEngineConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)
    geospatial: GeospatialConfig = Field(default_factory=GeospatialConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)

    @field_validator("BLOCKED_IPS", "ALLOWED_ORIGINS", "GRACE_ALLOWED_MODELS", mode="before")
    def _normalize_list_fields(cls, value: Any) -> List[str]:
        return _parse_csv_list(value)

    @model_validator(mode="after")
    def _sync_nested_models(self):
        self.earth_engine.service_account = self.GEE_SERVICE_ACCOUNT or self.earth_engine.service_account
        self.earth_engine.private_key = self.GEE_PRIVATE_KEY or self.earth_engine.private_key
        self.earth_engine.project_id = self.GEE_PROJECT_ID or self.earth_engine.project_id

        self.gemini.api_key = self.GEMINI_API_KEY or self.gemini.api_key
        self.gemini.model = self.GEMINI_MODEL or self.gemini.model
        self.gemini.vision_model = self.GEMINI_VISION_MODEL or self.gemini.vision_model
        self.gemini.temperature = self.GEMINI_TEMPERATURE or self.gemini.temperature
        self.gemini.max_output_tokens = self.GEMINI_MAX_OUTPUT_TOKENS or self.gemini.max_output_tokens
        self.gemini.top_p = self.GEMINI_TOP_P or self.gemini.top_p
        self.gemini.top_k = self.GEMINI_TOP_K or self.gemini.top_k

        self.auth.jwt_secret = self.JWT_SECRET or self.auth.jwt_secret
        self.auth.jwt_algorithm = self.JWT_ALGORITHM or self.auth.jwt_algorithm
        self.auth.jwt_secret_1 = self.JWT_SECRET_1 or self.auth.jwt_secret_1
        self.auth.jwt_secret_2 = self.JWT_SECRET_2 or self.auth.jwt_secret_2
        self.auth.auth_api_base = self.AUTH_API_BASE or self.auth.auth_api_base
        self.auth.auth_internal_base = self.AUTH_INTERNAL_BASE or self.auth.auth_internal_base
        self.auth.internal_secret = self.AI_GATEWAY_INTERNAL_SECRET or self.auth.internal_secret
        self.auth.internal_service_name = self.INTERNAL_SERVICE_NAME or self.auth.internal_service_name
        self.auth.api_keys = self.auth.api_keys or _parse_csv_list(self.API_KEYS)
        self.auth.auth_mode = self.AUTH_MODE or self.auth.auth_mode

        self.redis.host = self.REDIS_HOST or self.redis.host
        self.redis.port = self.REDIS_PORT or self.redis.port
        self.redis.password = self.REDIS_PASSWORD or self.redis.password
        self.redis.url = self.REDIS_URL or self.redis.url
        self.redis.max_connections = self.REDIS_MAX_CONNECTIONS or self.redis.max_connections
        self.redis.socket_timeout = self.REDIS_SOCKET_TIMEOUT or self.redis.socket_timeout
        self.redis.connect_timeout = self.REDIS_CONNECT_TIMEOUT or self.redis.connect_timeout

        self.rate_limit.per_minute = self.RATE_LIMIT_PER_MINUTE or self.rate_limit.per_minute
        self.rate_limit.anonymous_per_minute = self.RATE_LIMIT_ANONYMOUS_PER_MINUTE or self.rate_limit.anonymous_per_minute
        self.rate_limit.max_entries = self.GLOBAL_MAX_CONCURRENT_REQUESTS or self.rate_limit.max_entries

        self.geospatial.ndvi_water_threshold = self.NDVI_WATER_THRESHOLD or self.geospatial.ndvi_water_threshold
        self.geospatial.ndvi_sparse_threshold = self.NDVI_SPARSE_THRESHOLD or self.geospatial.ndvi_sparse_threshold
        self.geospatial.ndvi_moderate_threshold = self.NDVI_MODERATE_THRESHOLD or self.geospatial.ndvi_moderate_threshold
        self.geospatial.ndvi_dense_threshold = self.NDVI_DENSE_THRESHOLD or self.geospatial.ndvi_dense_threshold
        self.geospatial.change_detection_threshold = self.CHANGE_DETECTION_THRESHOLD or self.geospatial.change_detection_threshold
        self.geospatial.max_area_hectares = self.MAX_AREA_HECTARES or self.geospatial.max_area_hectares

        self.storage.bucket_name = self.GCS_BUCKET_NAME or self.storage.bucket_name
        self.storage.bigquery_dataset = self.BIGQUERY_DATASET or self.storage.bigquery_dataset
        self.storage.gcp_project_id = self.GCP_PROJECT_ID or self.storage.gcp_project_id

        return self

    def get_jwt_secrets(self) -> List[str]:
        secrets: List[str] = []
        if self.JWT_SECRET:
            secrets.append(self.JWT_SECRET)
        if self.JWT_SECRET_1:
            secrets.append(self.JWT_SECRET_1)
        if self.JWT_SECRET_2:
            secrets.append(self.JWT_SECRET_2)
        return secrets

    def get_gee_credentials(self):
        if not self.GEE_SERVICE_ACCOUNT or not self.GEE_PRIVATE_KEY:
            return None

        try:
            import ee
            if self.GEE_PRIVATE_KEY.strip().startswith("{"):
                return ee.ServiceAccountCredentials(
                    self.GEE_SERVICE_ACCOUNT,
                    key_data=json.loads(self.GEE_PRIVATE_KEY)
                )

            key_path = Path(self.GEE_PRIVATE_KEY)
            if key_path.exists():
                return ee.ServiceAccountCredentials(
                    self.GEE_SERVICE_ACCOUNT,
                    key_file=str(key_path)
                )
        except Exception:
            pass

        return None


settings = Settings()


def get_config() -> Settings:
    return settings
