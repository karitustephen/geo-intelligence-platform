"""
Standardized response formatters
"""

from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class StandardResponse(BaseModel):
    """Standard API response format"""
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    message: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PaginatedResponse(BaseModel):
    """Paginated response format"""
    items: List[Any]
    total: int
    page: int
    page_size: int
    has_next: bool
    has_prev: bool


class GeoJSONResponse(BaseModel):
    """GeoJSON response format"""
    type: str = "FeatureCollection"
    features: List[Dict[str, Any]] = Field(default_factory=list)
    properties: Dict[str, Any] = Field(default_factory=dict)


def success_response(
    data: Any = None,
    message: Optional[str] = None,
    metadata: Optional[Dict] = None,
    status_code: int = 200
) -> JSONResponse:
    """Create a success response"""
    response = StandardResponse(
        success=True,
        data=data,
        message=message,
        metadata=metadata or {}
    )
    return JSONResponse(status_code=status_code, content=response.model_dump())


def error_response(
    error: str,
    error_code: str = "INTERNAL_ERROR",
    message: Optional[str] = None,
    details: Optional[Dict] = None,
    status_code: int = 500
) -> JSONResponse:
    """Create an error response"""
    response = StandardResponse(
        success=False,
        error=error,
        error_code=error_code,
        message=message,
        metadata=details or {}
    )
    return JSONResponse(status_code=status_code, content=response.model_dump())


def paginated_response(
    items: List[Any],
    total: int,
    page: int,
    page_size: int
) -> Dict[str, Any]:
    """Create a paginated response"""
    return PaginatedResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        has_next=page * page_size < total,
        has_prev=page > 1
    ).model_dump()


def geojson_response(
    features: List[Dict[str, Any]],
    properties: Optional[Dict] = None
) -> Dict[str, Any]:
    """Create a GeoJSON response"""
    return GeoJSONResponse(
        features=features,
        properties=properties or {}
    ).model_dump()
