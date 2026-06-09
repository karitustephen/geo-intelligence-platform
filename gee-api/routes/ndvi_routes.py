"""
NDVI analysis routes for the geospatial intelligence API
"""

from fastapi import APIRouter, Depends, HTTPException

from middleware.auth_middleware import require_verified_user
from models import NDVIRequest, NDVITimeSeriesRequest, NDVITrendRequest, ChangeDetectionRequest
from services.ndvi_service import NDVIService
from utils.response import success_response, error_response

router = APIRouter(prefix="/api/ndvi", tags=["NDVI Analysis"])
ndvi_service = NDVIService()


@router.post("/calculate")
async def calculate_ndvi(
    request: NDVIRequest,
    user: dict = Depends(require_verified_user)
):
    """
    Calculate NDVI for a specific location and date
    
    NDVI values range from -1 to 1:
    - < 0: Water
    - 0 - 0.2: Barren/Urban
    - 0.2 - 0.4: Sparse vegetation
    - 0.4 - 0.6: Moderate vegetation
    - > 0.6: Dense vegetation
    """
    try:
        result = await ndvi_service.calculate_ndvi(request)
        return success_response(
            data=result,
            metadata={
                "user_id": user.get("user_id"),
                "model": "NDVI"
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        return error_response(
            error=str(e),
            error_code="NDVI_CALCULATION_FAILED",
            status_code=500        )


@router.post("/time-series")
async def get_ndvi_time_series(
    request: NDVITimeSeriesRequest,
    user: dict = Depends(require_verified_user)
):
    """
    Get NDVI time series for a location over a date range
    """
    try:
        result = await ndvi_service.get_time_series(request)
        return success_response(
            data=result,
            metadata={"user_id": user.get("user_id")}
        )
    except Exception as e:
        return error_response(
            error=str(e),
            error_code="TIME_SERIES_FAILED",
            status_code=500
        )


@router.post("/trend")
async def analyze_ndvi_trend(
    request: NDVITrendRequest,
    user: dict = Depends(require_verified_user)
):
    """
    Analyze NDVI trend over time with statistical significance
    """
    try:
        result = await ndvi_service.analyze_trend(request)
        return success_response(
            data=result,
            metadata={"user_id": user.get("user_id")}
        )
    except Exception as e:
        return error_response(
            error=str(e),
            error_code="TREND_ANALYSIS_FAILED",
            status_code=500
        )


@router.post("/change-detection")
async def detect_change(
    request: ChangeDetectionRequest,
    user: dict = Depends(require_verified_user)
):
    """
    Detect environmental change between two time periods
    
    Supports NDVI (vegetation) and NDWI (water) indices
    """
    try:
        result = await ndvi_service.detect_change(request)
        return success_response(
            data=result,
            metadata={
                "index": request.index,
                "user_id": user.get("user_id")
            }
        )
    except Exception as e:
        return error_response(
            error=str(e),
            error_code="CHANGE_DETECTION_FAILED",
            status_code=500
        )