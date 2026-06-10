"""
Request Logging Middleware with Context Propagation
"""

import time
import uuid
import json
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from logging_config import get_logger, set_request_id, clear_request_id
from utils.response import error_response

logger = get_logger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Add request ID to each request for tracing"""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        request.state.user_id = getattr(request.state, "user", {}).get("user_id", "anonymous")
        set_request_id(request_id, request.state.user_id)

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Structured access logging with metrics"""

    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        noisy_paths = ["/health", "/healthz", "/ready", "/ping", "/metrics"]
        is_noisy = request.url.path in noisy_paths

        if not is_noisy:
            setattr(request.app.state, "active_requests", getattr(request.app.state, "active_requests", 0) + 1)

        try:
            response = await call_next(request)
            duration_ms = (time.perf_counter() - start) * 1000

            if not is_noisy:
                user_id = getattr(request.state, "user", {}).get("user_id", "anonymous")
                request_id = getattr(request.state, "request_id", "unknown")
                log_data = {
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round(duration_ms, 2),
                    "user_id": str(user_id)[:30],
                    "ip": request.client.host if request.client else "unknown"
                }

                if response.status_code >= 500:
                    logger.error(json.dumps(log_data))
                elif response.status_code >= 400:
                    logger.warning(json.dumps(log_data))
                else:
                    logger.info(json.dumps(log_data))

            return response

        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.error(f"Request failed: {request.method} {request.url.path} - {str(e)}")
            return JSONResponse(status_code=500, content=error_response("Internal server error", 500).model_dump())

        finally:
            if not is_noisy:
                current = getattr(request.app.state, "active_requests", 1)
                request.app.state.active_requests = max(0, current - 1)
            clear_request_id()


class ErrorLoggingMiddleware(BaseHTTPMiddleware):
    """Capture and log exceptions with context"""

    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as e:
            request_id = getattr(request.state, "request_id", "unknown")
            logger.exception(f"[{request_id}] Unhandled exception: {request.method} {request.url.path} - {str(e)}")
            raise
