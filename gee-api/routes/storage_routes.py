"""
Storage and data persistence routes for the geospatial intelligence API
"""

from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from typing import List

from middleware.auth_middleware import require_verified_user
from models import ExportRequest
from services.storage_service import StorageService
from utils.response import success_response, error_response

router = APIRouter(prefix="/api/storage", tags=["Storage"])
storage_service = StorageService()


@router.post("/upload")
async def upload_geotiff(
    file: UploadFile = File(...),
    user: dict = Depends(require_verified_user)
):
    """
    Upload GeoTIFF file for analysis
    
    Supports GeoTIFF, TIFF, and Cloud Optimized GeoTIFF (COG) formats
    """
    try:
        result = await storage_service.upload_geotiff(file, user.get("user_id"))
        return success_response(
            data=result,
            metadata={"user_id": user.get("user_id")}
        )
    except HTTPException:
        raise
    except Exception as e:
        return error_response(
            error=str(e),
            error_code="UPLOAD_FAILED",
            status_code=500
        )


@router.get("/analysis/{analysis_id}")
async def get_analysis(
    analysis_id: str,
    user: dict = Depends(require_verified_user)
):
    """
    Retrieve stored analysis results by ID
    """
    try:
        result = await storage_service.get_analysis(analysis_id, user.get("user_id"))
        if not result:
            return error_response(
                error="Analysis not found",
                error_code="NOT_FOUND",
                status_code=404
            )
        return success_response(data=result)
    except Exception as e:
        return error_response(
            error=str(e),
            error_code="RETRIEVAL_FAILED",
            status_code=500
        )


@router.get("/user/analyses")
async def list_user_analyses(
    user: dict = Depends(require_verified_user),
    limit: int = 50,
    offset: int = 0
):
    """
    List all analyses for the authenticated user
    """
    try:
        results = await storage_service.list_user_analyses(
            user.get("user_id"),
            limit=limit,
            offset=offset
        )
        return success_response(data=results)
    except Exception as e:
        return error_response(
            error=str(e),
            error_code="LIST_FAILED",
            status_code=500
        )


@router.delete("/analysis/{analysis_id}")
async def delete_analysis(
    analysis_id: str,
    user: dict = Depends(require_verified_user)
):
    """
    Delete a stored analysis
    """
    try:
        await storage_service.delete_analysis(analysis_id, user.get("user_id"))
        return success_response(message="Analysis deleted successfully")
    except Exception as e:
        return error_response(
            error=str(e),
            error_code="DELETION_FAILED",
            status_code=500
        )


@router.post("/export")
async def export_analysis(
    request: ExportRequest,
    user: dict = Depends(require_verified_user)
):
    """
    Export analysis results in various formats (GeoTIFF, GeoJSON, CSV)
    """
    try:
        result = await storage_service.export_analysis(
            request.analysis_id,
            request.format,
            user.get("user_id")
        )
        return success_response(data=result)
    except Exception as e:
        return error_response(
            error=str(e),
            error_code="EXPORT_FAILED",
            status_code=500
        )