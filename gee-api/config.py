import os
from dotenv import load_dotenv

load_dotenv()

"""
Configuration management for Geospatial Intelligence API
"""

import os
import json
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass
class EarthEngineConfig:
    """Earth Engine configuration"""
    service_account: str = ""
    private_key_path: str = ""
    private_key_json: Optional[Dict] = None
    project_id: str = ""
    
    @classmethod
    def from_env(cls) -> "EarthEngineConfig":
        private_key_path = os.getenv("GEE_PRIVATE_KEY_PATH", "")
        private_key_json_str = os.getenv("GEE_PRIVATE_KEY_JSON", "")
        private_key_json = json.loads(private_key_json_str) if private_key_json_str else None
        
        return cls(
            service_account=os.getenv("GEE_SERVICE_ACCOUNT", ""),
            private_key_path=private_key_path,
            private_key_json=private_key_json,
            project_id=os.getenv("GEE_PROJECT_ID", "")
        )


@dataclass
class GeminiConfig:
    """Google Gemini AI configuration"""
    api_key: str = ""
    model: str = "gemini-2.0-flash-exp"
    vision_model: str = "gemini-2.0-flash-exp"
    temperature: float = 0.7
    max_output_tokens: int = 8192
    top_p: float = 0.95
    top_k: int = 40
    
    @classmethod
    def from_env(cls) -> "GeminiConfig":
        return cls(
            api_key=os.getenv("GEMINI_API_KEY", ""),
            model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash-exp"),
            vision_model=os.getenv("GEMINI_VISION_MODEL", "gemini-2.0-flash-exp"),
            temperature=float(os.getenv("GEMINI_TEMPERATURE", "0.7")),
            max_output_tokens=int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "8192")),
            top_p=float(os.getenv("GEMINI_TOP_P", "0.95")),
            top_k=int(os.getenv("GEMINI_TOP_K", "40"))
        )


@dataclass
class AuthConfig:
    """Authentication configuration"""
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_secret_1: Optional[str] = None
    jwt_secret_2: Optional[str] = None
    auth_api_base: str = "https://api.arybit.co.ke"
    auth_internal_base: str = "http://auth-service:8001"
    internal_secret: str = ""
    internal_service_name: str = "arybit-geo-intelligence"
    api_keys: List[str] = field(default_factory=list)
    auth_mode: str = "remote"
    
    @classmethod
    def from_env(cls) -> "AuthConfig":
        api_keys_str = os.getenv("API_KEYS", "")
        api_keys = [k.strip() for k in api_keys_str.split(",") if k.strip()]
        
        return cls(
            jwt_secret=os.getenv("JWT_SECRET", ""),
            jwt_algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
            jwt_secret_1=os.getenv("JWT_SECRET_1"),
            jwt_secret_2=os.getenv("JWT_SECRET_2"),
            auth_api_base=os.getenv("AUTH_API_BASE", "https://api.arybit.co.ke"),
            auth_internal_base=os.getenv("AUTH_INTERNAL_BASE", "http://auth-service:8001"),
            internal_secret=os.getenv("AI_GATEWAY_INTERNAL_SECRET", ""),
            internal_service_name=os.getenv("INTERNAL_SERVICE_NAME", "arybit-geo-intelligence"),
            api_keys=api_keys,
            auth_mode=os.getenv("AUTH_MODE", "remote")
        )


@dataclass
class RedisConfig:
    """Redis configuration"""
    host: str = "localhost"
    port: int = 6379
    password: Optional[str] = None
    url: Optional[str] = None
    max_connections: int = 50
    socket_timeout: int = 10
    connect_timeout: int = 5
    ttl_seconds: int = 3600
    
    @classmethod
    def from_env(cls) -> "RedisConfig":
        return cls(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD"),
            url=os.getenv("REDIS_URL"),
            max_connections=int(os.getenv("REDIS_MAX_CONNECTIONS", "50")),
            socket_timeout=int(os.getenv("REDIS_SOCKET_TIMEOUT", "10")),
            connect_timeout=int(os.getenv("REDIS_CONNECT_TIMEOUT", "5")),
            ttl_seconds=int(os.getenv("CACHE_TTL_SECONDS", "3600"))
        )


@dataclass
class RateLimitConfig:
    """Rate limiting configuration"""
    per_minute: int = 60
    anonymous_per_minute: int = 30
    max_entries: int = 50000
    
    @classmethod
    def from_env(cls) -> "RateLimitConfig":
        return cls(
            per_minute=int(os.getenv("RATE_LIMIT_PER_MINUTE", "60")),
            anonymous_per_minute=int(os.getenv("RATE_LIMIT_ANONYMOUS_PER_MINUTE", "30")),
            max_entries=int(os.getenv("RATE_LIMIT_MAX_ENTRIES", "50000"))
        )


@dataclass
class GeospatialConfig:
    """Geospatial analysis configuration"""
    ndvi_water_threshold: float = 0.0
    ndvi_sparse_threshold: float = 0.2
    ndvi_moderate_threshold: float = 0.4
    ndvi_dense_threshold: float = 0.6
    change_detection_threshold: float = 0.15
    max_area_hectares: float = 10000.0
    max_time_range_days: int = 365
    
    @classmethod
    def from_env(cls) -> "GeospatialConfig":
        return cls(
            ndvi_water_threshold=float(os.getenv("NDVI_WATER_THRESHOLD", "0.0")),
            ndvi_sparse_threshold=float(os.getenv("NDVI_SPARSE_THRESHOLD", "0.2")),
            ndvi_moderate_threshold=float(os.getenv("NDVI_MODERATE_THRESHOLD", "0.4")),
            ndvi_dense_threshold=float(os.getenv("NDVI_DENSE_THRESHOLD", "0.6")),
            change_detection_threshold=float(os.getenv("CHANGE_DETECTION_THRESHOLD", "0.15")),
            max_area_hectares=float(os.getenv("MAX_AREA_HECTARES", "10000.0")),
            max_time_range_days=int(os.getenv("MAX_TIME_RANGE_DAYS", "365"))
        )


@dataclass
class StorageConfig:
    """Storage configuration"""
    bucket_name: str = "geo-intelligence-data"
    bigquery_dataset: str = "geospatial_analytics"
    gcp_project_id: str = ""
    
    @classmethod
    def from_env(cls) -> "StorageConfig":
        return cls(
            bucket_name=os.getenv("GCS_BUCKET_NAME", "geo-intelligence-data"),
            bigquery_dataset=os.getenv("BIGQUERY_DATASET", "geospatial_analytics"),
            gcp_project_id=os.getenv("GCP_PROJECT_ID", "")
        )


@dataclass
class AppConfig:
    """Main application configuration"""
    app_name: str = "Arybit Geospatial Intelligence"
    app_version: str = "2.0.0"
    environment: str = "production"
    debug: bool = False
    log_level: str = "INFO"
    port: int = 8000
    workers: int = 4
    global_max_concurrent: int = 50
    
    # Sub-configurations
    earth_engine: EarthEngineConfig = field(default_factory=EarthEngineConfig)
    gemini: GeminiConfig = field(default_factory=GeminiConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    geospatial: GeospatialConfig = field(default_factory=GeospatialConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    
    @classmethod
    def from_env(cls) -> "AppConfig":
        return cls(
            app_name=os.getenv("APP_NAME", "Arybit Geospatial Intelligence"),
            app_version=os.getenv("APP_VERSION", "2.0.0"),
            environment=os.getenv("ENVIRONMENT", "production"),
            debug=os.getenv("DEBUG", "false").lower() == "true",
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            port=int(os.getenv("PORT", "8000")),
            workers=int(os.getenv("WORKERS", "4")),
            global_max_concurrent=int(os.getenv("GLOBAL_MAX_CONCURRENT", "50")),
            earth_engine=EarthEngineConfig.from_env(),
            gemini=GeminiConfig.from_env(),
            auth=AuthConfig.from_env(),
            redis=RedisConfig.from_env(),
            rate_limit=RateLimitConfig.from_env(),
            geospatial=GeospatialConfig.from_env(),
            storage=StorageConfig.from_env()
        )


@lru_cache()
def get_config() -> AppConfig:
    """Get cached application configuration"""
    return AppConfig.from_env()
