"""
Health check routes for the geospatial intelligence API
"""

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from middleware.auth_middleware import get_current_user
from utils.response import success_response, error_response

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("")
async def health_check():
    """Comprehensive health check"""
    return success_response(
        data={
            "status": "healthy",
            "service": "Arybit Geospatial Intelligence",
            "version": "2.0.0",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    )


@router.get("/ready")
async def readiness_check():
    """Readiness probe for orchestration"""
    return success_response(data={"status": "ready"})


@router.get("/live")
async def liveness_check():
    """Liveness probe for orchestration"""
    return success_response(data={"status": "alive"})


@router.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint"""
    from prometheus_client import REGISTRY
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST
    )


@router.get("/debug/pools")
async def debug_pools(user: dict = Depends(get_current_user)):
    """Debug endpoint for connection pool status (admin only)"""
    if user.get("role") != "system":
        return error_response(error="Admin access required", status_code=403)
    
    return success_response(data={
        "message": "Debug pools endpoint - implement as needed"
    })