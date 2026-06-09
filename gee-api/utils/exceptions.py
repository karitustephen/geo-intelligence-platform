"""
Custom exception classes for the geospatial intelligence API
"""

from typing import Optional, Dict, Any


class GeoIntelligenceError(Exception):
	"""Base exception for geospatial intelligence platform"""
    
	def __init__(
		self,
		message: str,
		code: str = "INTERNAL_ERROR",
		status_code: int = 500,
		details: Optional[Dict[str, Any]] = None
	):
		self.message = message
		self.code = code
		self.status_code = status_code
		self.details = details or {}
		super().__init__(self.message)


class AuthenticationError(GeoIntelligenceError):
	"""Authentication related errors"""
    
	def __init__(self, message: str = "Authentication required", details: Optional[Dict] = None):
		super().__init__(
			message=message,
			code="AUTHENTICATION_ERROR",
			status_code=401,
			details=details
		)


class AuthorizationError(GeoIntelligenceError):
	"""Authorization/permission errors"""
    
	def __init__(self, message: str = "Insufficient permissions", details: Optional[Dict] = None):
		super().__init__(
			message=message,
			code="AUTHORIZATION_ERROR",
			status_code=403,
			details=details
		)


class RateLimitError(GeoIntelligenceError):
	"""Rate limit exceeded errors"""
    
	def __init__(
		self,
		message: str = "Rate limit exceeded",
		limit: int = 60,
		remaining: int = 0,
		reset: int = 60
	):
		super().__init__(
			message=message,
			code="RATE_LIMIT_EXCEEDED",
			status_code=429,
			details={
				"limit": limit,
				"remaining": remaining,
				"reset_seconds": reset
			}
		)


class ValidationError(GeoIntelligenceError):
	"""Request validation errors"""
    
	def __init__(self, message: str, field: Optional[str] = None):
		details = {"field": field} if field else {}
		super().__init__(
			message=message,
			code="VALIDATION_ERROR",
			status_code=422,
			details=details
		)


class NotFoundError(GeoIntelligenceError):
	"""Resource not found errors"""
    
	def __init__(self, resource_type: str, resource_id: str):
		super().__init__(
			message=f"{resource_type} with id '{resource_id}' not found",
			code="NOT_FOUND",
			status_code=404,
			details={"resource_type": resource_type, "resource_id": resource_id}
		)


class EarthEngineError(GeoIntelligenceError):
	"""Earth Engine API errors"""
    
	def __init__(self, message: str, operation: str):
		super().__init__(
			message=f"Earth Engine {operation} failed: {message}",
			code="EARTH_ENGINE_ERROR",
			status_code=503,
			details={"operation": operation}
		)


class GeminiError(GeoIntelligenceError):
	"""Gemini AI API errors"""
    
	def __init__(self, message: str):
		super().__init__(
			message=f"Gemini AI error: {message}",
			code="GEMINI_ERROR",
			status_code=503
		)


class StorageError(GeoIntelligenceError):
	"""Storage/Cloud Storage errors"""
    
	def __init__(self, message: str, bucket: str):
		super().__init__(
			message=f"Storage error in bucket '{bucket}': {message}",
			code="STORAGE_ERROR",
			status_code=500,
			details={"bucket": bucket}
		)


class CircuitBreakerOpenError(GeoIntelligenceError):
	"""Circuit breaker is open"""
    
	def __init__(self, service: str, retry_after: int = 30):
		super().__init__(
			message=f"Service '{service}' is temporarily unavailable",
			code="CIRCUIT_BREAKER_OPEN",
			status_code=503,
			details={"service": service, "retry_after": retry_after}
		)


class QuotaExceededError(GeoIntelligenceError):
	"""User quota exceeded"""
    
	def __init__(self, quota_type: str, limit: int, used: int):
		super().__init__(
			message=f"{quota_type} quota exceeded: {used}/{limit}",
			code="QUOTA_EXCEEDED",
			status_code=429,
			details={"quota_type": quota_type, "limit": limit, "used": used}
		)

