"""
Pydantic data schemas for the Arybit Geospatial Intelligence API.
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class Coordinate(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class BoundingBox(BaseModel):
    min_lat: float = Field(..., ge=-90, le=90)
    max_lat: float = Field(..., ge=-90, le=90)
    min_lon: float = Field(..., ge=-180, le=180)
    max_lon: float = Field(..., ge=-180, le=180)

    def dict(self, *args, **kwargs) -> Dict[str, Any]:
        return {
            "min_lat": self.min_lat,
            "max_lat": self.max_lat,
            "min_lon": self.min_lon,
            "max_lon": self.max_lon,
        }


class TimeRange(BaseModel):
    start_date: str
    end_date: str


class NDVIRequest(BaseModel):
    location: Coordinate
    date: str
    buffer_meters: int = 100
    satellite: str = "sentinel"


class NDVITimeSeriesRequest(BaseModel):
    location: Coordinate
    time_range: TimeRange
    satellite: str = "sentinel"
    buffer_meters: int = 100


class NDVITrendRequest(BaseModel):
    location: Coordinate
    time_range: TimeRange
    satellite: str = "sentinel"
    buffer_meters: int = 100


class ChangeDetectionRequest(BaseModel):
    region: BoundingBox
    time_range: TimeRange
    index: str = "ndvi"
    threshold: float = 0.15


class EnvironmentalInsightRequest(BaseModel):
    query: str
    context_data: Optional[Dict[str, Any]] = None
    model: Optional[str] = None
    stream: bool = False


class VegetationHealthRequest(BaseModel):
    location: Coordinate
    time_range: TimeRange
    metrics: List[str] = ["ndvi", "evi", "ndmi"]


class WildfireRiskRequest(BaseModel):
    region: BoundingBox
    date: str


class ChangeInterpretationRequest(BaseModel):
    change_data: Dict[str, Any]
    region: BoundingBox
    time_range: TimeRange


class ForecastRequest(BaseModel):
    location: Coordinate
    historical_days: int = 30
    forecast_days: int = 30
    historical_data: List[Dict[str, Any]] = Field(default_factory=list)


class ExportRequest(BaseModel):
    analysis_id: str
    format: str = Field("geojson")
