"""
NDVI analysis service with Earth Engine integration
"""

import ee
import numpy as np
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

from config import get_config
from utils.exceptions import EarthEngineError, ValidationError


class NDVIService:
    """Service for NDVI calculation and analysis"""
    
    def __init__(self):
        self.config = get_config()
        self._ee_initialized = False
    
    def _init_ee(self):
        """Initialize Earth Engine"""
        if self._ee_initialized:
            return
        
        try:
            credentials = self.config.earth_engine.get_credentials()
            if credentials:
                ee.Initialize(credentials, project=self.config.earth_engine.project_id)
            else:
                ee.Initialize(project=self.config.earth_engine.project_id)
            self._ee_initialized = True
        except Exception as e:
            raise EarthEngineError(str(e), "initialize")
    
    async def calculate_ndvi(self, request) -> Dict[str, Any]:
        """Calculate NDVI for a location"""
        self._init_ee()
        
        point = ee.Geometry.Point([request.location.lon, request.location.lat])
        
        # Get satellite image
        if request.satellite.lower() == "sentinel":
            collection = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
        else:
            collection = ee.ImageCollection('LANDSAT/LC08/C02/T1_L2')
        
        collection = collection.filterDate(request.date, request.date)
        image = collection.first()
        
        if not image:
            raise ValidationError(f"No imagery found for date {request.date}", "date")
        
        # Calculate NDVI
        if request.satellite.lower() == "sentinel":
            ndvi = image.normalizedDifference(['B8', 'B4']).rename('NDVI')
        else:
            ndvi = image.normalizedDifference(['B5', 'B4']).rename('NDVI')
        
        # Extract value
        value = ndvi.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=point.buffer(request.buffer_meters),
            scale=10,
            bestEffort=True
        ).get('NDVI').getInfo()
        
        # Classify NDVI
        ndvi_value = round(float(value), 4) if value else None
        classification = self._classify_ndvi(ndvi_value)
        
        return {
            "ndvi": ndvi_value,
            "classification": classification,
            "location": request.location.dict(),
            "date": request.date,
            "satellite": request.satellite,
            "buffer_meters": request.buffer_meters
        }
    
    def _classify_ndvi(self, value: Optional[float]) -> str:
        """Classify NDVI value"""
        if value is None:
            return "unknown"
        if value < self.config.geospatial.ndvi_water_threshold:
            return "water"
        if value < self.config.geospatial.ndvi_sparse_threshold:
            return "barren"
        if value < self.config.geospatial.ndvi_moderate_threshold:
            return "sparse_vegetation"
        if value < self.config.geospatial.ndvi_dense_threshold:
            return "moderate_vegetation"
        return "dense_vegetation"
    
    async def detect_change(self, request) -> Dict[str, Any]:
        """Detect environmental change between two periods"""
        self._init_ee()
        
        region = ee.Geometry.Rectangle([
            request.region.min_lon, request.region.min_lat,
            request.region.max_lon, request.region.max_lat
        ])
        
        # Split time range
        start = datetime.fromisoformat(request.time_range.start_date)
        end = datetime.fromisoformat(request.time_range.end_date)
        mid = start + (end - start) / 2
        mid_str = mid.strftime("%Y-%m-%d")
        
        # Get image collections
        collection = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
        before = collection.filterDate(request.time_range.start_date, mid_str).median()
        after = collection.filterDate(mid_str, request.time_range.end_date).median()
        
        # Calculate index
        if request.index == "ndvi":
            before_idx = before.normalizedDifference(['B8', 'B4'])
            after_idx = after.normalizedDifference(['B8', 'B4'])
        elif request.index == "ndwi":
            before_idx = before.normalizedDifference(['B3', 'B8'])
            after_idx = after.normalizedDifference(['B3', 'B8'])
        else:
            raise ValidationError(f"Invalid index: {request.index}", "index")
        
        # Calculate change
        difference = after_idx.subtract(before_idx).abs()
        change_mask = difference.gt(request.threshold)
        
        # Calculate statistics
        stats = change_mask.reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=region,
            scale=10,
            bestEffort=True,
            maxPixels=1e9
        )
        
        total_pixels = stats.get('sum').getInfo() or 0
        area_ha = self._calculate_area_hectares(request.region)
        change_ha = total_pixels * 0.01
        percent_change = (change_ha / area_ha * 100) if area_ha > 0 else 0
        
        # Determine severity
        if percent_change < 5:
            severity = "low"
        elif percent_change < 15:
            severity = "medium"
        elif percent_change < 30:
            severity = "high"
        else:
            severity = "critical"
        
        # Generate recommendations
        recommendations = self._get_change_recommendations(request.index, severity, percent_change)
        
        return {
            "total_change_ha": round(change_ha, 2),
            "percent_change": round(percent_change, 1),
            "severity": severity,
            "recommendations": recommendations,
            "time_range": request.time_range.dict(),
            "region": request.region.dict()
        }
    
    def _calculate_area_hectares(self, region) -> float:
        """Calculate region area in hectares"""
        lat_diff = abs(region.max_lat - region.min_lat)
        lon_diff = abs(region.max_lon - region.min_lon)
        width_km = lon_diff * 111 * np.cos(np.radians((region.max_lat + region.min_lat) / 2))
        height_km = lat_diff * 111
        return (width_km * height_km) * 100
    
    def _get_change_recommendations(self, index: str, severity: str, percent: float) -> List[str]:
        """Generate recommendations based on change detection"""
        recommendations = []
        
        if index == "ndvi":
            if severity == "critical":
                recommendations.append("Severe vegetation loss detected - immediate intervention required")
                recommendations.append("Schedule field verification and assess erosion risk")
            elif severity == "high":
                recommendations.append("Significant vegetation decline - investigate causes")
            else:
                recommendations.append("Continue monitoring - implement bi-weekly NDVI tracking")
        
        if index == "ndwi" and percent > 20:
            recommendations.append("Water body change detected - conduct hydrological assessment")
        
        return recommendations or ["Continue regular monitoring"]