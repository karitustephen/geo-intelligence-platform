from flask import Flask
from routes.health_routes import health_bp
from routes.ndvi_routes import ndvi_bp
from logging_config import setup_logging
from config import Config

def create_app():
    # Initialize Logging
    setup_logging()
    
    app = Flask(__name__)
    app.config.from_object(Config)

    # Register Blueprints (Routes)
    app.register_blueprint(health_bp)
    app.register_blueprint(ndvi_bp)

    return app

app = create_app()"""
Arybit Geospatial Intelligence CORE - Environmental Monitoring Platform
FASTAPI_AI_GATEWAY_URL: https://geo.arybit.co.ke/

Production-grade geospatial intelligence platform with Google Earth Engine integration,
satellite imagery analysis, change detection, and environmental monitoring.
"""
from __future__ import annotations

import os
import redis.asyncio as aioredis
import asyncio
import hashlib
import base64
import re
import ipaddress
import logging
import socket
from functools import lru_cache
import json
from urllib.parse import urlparse, urlunparse, quote
import uuid
import io # Used for docx, rtf
from PIL import Image # Required by pdf2image
import tempfile
import time
import random
from enum import Enum
from collections import defaultdict, deque, OrderedDict
from contextlib import asynccontextmanager
from typing import Optional, List, Set, Any, Annotated, Dict, Tuple
from datetime import datetime, timezone, timedelta
import numpy as np
from dataclasses import dataclass, field

# Geospatial imports
import ee
import geemap
from geemap import geemap as geemap_core
import geopandas as gpd
from shapely.geometry import Point, Polygon, mapping
from shapely import wkt
import rasterio
from rasterio.transform import from_bounds
import xarray as xr
import rioxarray

# Machine Learning for geospatial
from sklearn.ensemble import RandomForestRegressor, RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
import joblib

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

import httpx
from pydantic import ConfigDict, model_validator, AliasChoices
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException, Request, Depends, Query, Body, status, Response, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.background import BackgroundTask # For non-blocking background tasks
import uvicorn
import threading
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Counter, Gauge, Histogram, REGISTRY
from pydantic_settings import BaseSettings, SettingsConfigDict

from config import settings # Import the new settings

OLLAMA_HOST = settings.ollama_host

# ============================================================
# OPENTELEMETRY TRACING SETUP
# ============================================================
try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    resource = Resource.create({"service.name": os.getenv("INTERNAL_SERVICE_NAME", "arybit-ai-gateway")})
    trace.set_tracer_provider(TracerProvider(resource=resource))
    
    # Production Hardening: Disable console exporter by default to avoid log flooding
    if os.getenv("OTEL_CONSOLE_EXPORTER", "false").lower() == "true":
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter
        trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        
    tracer = trace.get_tracer(__name__)
    OTEL_ENABLED = True
except ImportError:
    tracer = None
    OTEL_ENABLED = False

class GeospatialSettings(BaseSettings):
    """Geospatial intelligence configuration"""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    
    # API Configuration
    app_name: str = "Arybit Geospatial Intelligence"
    app_version: str = "1.0.0"
    environment: str = "production"
    
    # Google Earth Engine
    gee_service_account: str = ""
    gee_private_key_path: str = ""
    gee_project_id: str = ""
    
    # Google Cloud
    gcp_project_id: str = ""
    gcp_location: str = "us-central1"
    vertex_ai_endpoint: str = ""
    bigquery_dataset: str = "geospatial_analytics"
    
    # Model Configuration
    change_detection_model: str = "random_forest"
    land_cover_model: str = "sentinel_2_classifier"
    ndvi_threshold_normal: float = 0.4
    ndvi_threshold_dense: float = 0.6
    water_detection_threshold: float = 0.3
    
    # API Limits
    max_area_hectares: float = 10000.0
    max_time_range_days: int = 365
    rate_limit_per_minute: int = 60
    global_max_concurrent_requests: int = 20
    
    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: Optional[str] = None
    
    # Logging
    log_level: str = "INFO"
    log_format: str = "json"
    
    # Cache
    tile_cache_ttl_seconds: int = 3600
    analysis_cache_ttl_seconds: int = 86400

settings = GeospatialSettings()

# ============================================================
# LOGGING SETUP
# ============================================================

logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Cache for system prompts - loaded once, reused forever
_SYSTEM_PROMPT_CACHE = {}

async def get_cached_system_prompt(vertical: str) -> str:
    """Get system prompt from cache instead of rebuilding every time"""
    if vertical not in _SYSTEM_PROMPT_CACHE:
        config = get_vertical_config(vertical)
        _SYSTEM_PROMPT_CACHE[vertical] = config["system"]
    return _SYSTEM_PROMPT_CACHE[vertical]

# ============================================================
# GEOSPATIAL DATA MODELS
# ============================================================

class Coordinate(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)

class BoundingBox(BaseModel):
    min_lat: float = Field(..., ge=-90, le=90)
    max_lat: float = Field(..., ge=-90, le=90)
    min_lon: float = Field(..., ge=-180, le=180)
    max_lon: float = Field(..., ge=-180, le=180)
    
    @property
    def area_hectares(self) -> float:
        """Calculate approximate area in hectares"""
        lat_diff = abs(self.max_lat - self.min_lat)
        lon_diff = abs(self.max_lon - self.min_lon)
        # Approximate: 1 degree ≈ 111 km
        width_km = lon_diff * 111 * np.cos(np.radians((self.max_lat + self.min_lat) / 2))
        height_km = lat_diff * 111
        return (width_km * height_km) * 100  # Convert km² to hectares

class TimeRange(BaseModel):
    start_date: str  # YYYY-MM-DD
    end_date: str    # YYYY-MM-DD

class NDVIRequest(BaseModel):
    location: Coordinate
    date: str
    buffer_meters: int = 100
    satellite: str = "sentinel"  # landsat or sentinel

class ChangeDetectionRequest(BaseModel):
    region: BoundingBox
    time_range: TimeRange
    index: str = "ndvi"  # ndvi, ndwi, ndbi
    threshold: float = 0.15
    algorithm: str = "difference"

class LandCoverRequest(BaseModel):
    region: BoundingBox
    date: str
    resolution: str = "10m"  # 10m, 20m, 60m
    include_probabilities: bool = False

class VegetationHealthRequest(BaseModel):
    location: Coordinate
    time_range: TimeRange
    metrics: List[str] = ["ndvi", "evi", "msavi2", "ndmi"]

class WaterQualityRequest(BaseModel):
    region: BoundingBox
    date: str
    parameters: List[str] = ["chlorophyll", "turbidity", "sst"]

class WildfireRiskRequest(BaseModel):
    region: BoundingBox
    date: str
    include_historical: bool = True

class TimeSeriesAnalysisRequest(BaseModel):
    location: Coordinate
    time_range: TimeRange
    metric: str = "ndvi"
    interval_days: int = 16
    forecast_days: int = 30

class GeospatialAnalysisResponse(BaseModel):
    success: bool
    data: dict
    metadata: dict
    timestamp: str

class ChangeDetectionResult(BaseModel):
    total_change_ha: float
    percent_change: float
    change_map: Optional[str]  # Base64 encoded PNG
    severity: str  # low, medium, high, critical
    recommendations: List[str]

class TimeSeriesPoint(BaseModel):
    date: str
    value: float
    quality_flag: Optional[str] = None

class TimeSeriesResponse(BaseModel):
    metric: str
    values: List[TimeSeriesPoint]
    trend: str  # increasing, decreasing, stable
    trend_pct: float
    forecast: Optional[List[TimeSeriesPoint]] = None

# ============================================================
# EARTH ENGINE INITIALIZATION
# ============================================================

class EarthEngineClient:
    """Singleton Earth Engine client with authentication"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def initialize(self):
        """Initialize Earth Engine with service account"""
        if self._initialized:
            return
        
        try:
            if settings.gee_service_account and settings.gee_private_key_path:
                credentials = ee.ServiceAccountCredentials(
                    settings.gee_service_account,
                    settings.gee_private_key_path
                )
                ee.Initialize(credentials, project=settings.gee_project_id)
            else:
                # Try anonymous initialization (limited access)
                ee.Initialize(project=settings.gee_project_id)
            
            self._initialized = True
            logger.info("Google Earth Engine initialized successfully")
        except Exception as e:
            logger.error(f"Earth Engine initialization failed: {e}")
            raise
    
    @property
    def is_ready(self) -> bool:
        return self._initialized
    
    def get_image_collection(self, satellite: str, start_date: str, end_date: str):
        """Get satellite image collection for time range"""
        if satellite.lower() == "sentinel":
            collection = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
        elif satellite.lower() == "landsat":
            collection = ee.ImageCollection('LANDSAT/LC08/C02/T1_L2')
        else:
            raise ValueError(f"Unknown satellite: {satellite}")
        
        return collection.filterDate(start_date, end_date).filterBounds(
            ee.Geometry.Point([0, 0])
        )
    
    def calculate_ndvi(self, image: ee.Image) -> ee.Image:
        """Calculate NDVI from satellite image"""
        # Sentinel-2 bands
        nir = image.select('B8')
        red = image.select('B4')
        ndvi = nir.subtract(red).divide(nir.add(red))
        return ndvi.rename('NDVI')
    
    def calculate_ndwi(self, image: ee.Image) -> ee.Image:
        """Calculate NDWI for water detection"""
        green = image.select('B3')
        nir = image.select('B8')
        ndwi = green.subtract(nir).divide(green.add(nir))
        return ndwi.rename('NDWI')
    
    def calculate_evi(self, image: ee.Image) -> ee.Image:
        """Calculate Enhanced Vegetation Index"""
        nir = image.select('B8')
        red = image.select('B4')
        blue = image.select('B2')
        evi = nir.subtract(red).multiply(2.5).divide(
            nir.add(red.multiply(6)).subtract(blue.multiply(7.5)).add(1)
        )
        return evi.rename('EVI')
    
    def calculate_msavi2(self, image: ee.Image) -> ee.Image:
        """Calculate Modified Soil Adjusted Vegetation Index 2"""
        nir = image.select('B8')
        red = image.select('B4')
        msavi2 = nir.add(1).subtract(
            (nir.add(1).pow(2).subtract(red.multiply(8))).sqrt()
        ).divide(2)
        return msavi2.rename('MSAVI2')
    
    def calculate_ndmi(self, image: ee.Image) -> ee.Image:
        """Calculate Normalized Difference Moisture Index"""
        nir = image.select('B8')
        swir = image.select('B11')
        ndmi = nir.subtract(swir).divide(nir.add(swir))
        return ndmi.rename('NDMI')

gee_client = EarthEngineClient()

# ============================================================
# GEOSPATIAL MACHINE LEARNING MODELS
# ============================================================

class GeospatialMLModels:
    """Machine learning models for environmental monitoring"""
    
    def __init__(self):
        self.land_cover_model = None
        self.change_detection_model = None
        self.wildfire_risk_model = None
        self.water_quality_model = None
        self._load_models()
    
    def _load_models(self):
        """Load pre-trained models or initialize new ones"""
        try:
            # Try to load saved models
            self.land_cover_model = joblib.load('/models/land_cover_classifier.pkl')
            self.change_detection_model = joblib.load('/models/change_detection.pkl')
            logger.info("Geospatial ML models loaded successfully")
        except Exception as e:
            logger.warning(f"Could not load saved models: {e}. Using fallback logic.")
            self._init_fallback_models()
    
    def _init_fallback_models(self):
        """Initialize fallback model pipelines"""
        self.land_cover_model = Pipeline([
            ('scaler', StandardScaler()),
            ('classifier', RandomForestClassifier(n_estimators=100, random_state=42))
        ])
        
        self.change_detection_model = Pipeline([
            ('scaler', StandardScaler()),
            ('regressor', RandomForestRegressor(n_estimators=100, random_state=42))
        ])
    
    async def predict_land_cover(self, features: np.ndarray) -> np.ndarray:
        """Predict land cover classification"""
        if self.land_cover_model:
            return self.land_cover_model.predict(features)
        return np.zeros(len(features))
    
    async def detect_change(self, before_features: np.ndarray, after_features: np.ndarray) -> np.ndarray:
        """Detect change between two time periods"""
        diff_features = np.abs(after_features - before_features)
        if self.change_detection_model:
            return self.change_detection_model.predict(diff_features)
        return np.mean(diff_features, axis=1)

ml_models = GeospatialMLModels()

# ============================================================
# GEOSPATIAL ANALYSIS SERVICE
# ============================================================

class GeospatialAnalysisService:
    """Core geospatial analysis service with Earth Engine integration"""
    
    def __init__(self):
        self.cache = {}
        self.cache_ttl = settings.analysis_cache_ttl_seconds
    
    async def get_ndvi(self, request: NDVIRequest) -> dict:
        """Calculate NDVI for a specific location"""
        gee_client.initialize()
        
        # Create point geometry
        point = ee.Geometry.Point([request.location.lon, request.location.lat])
        
        # Get satellite image
        collection = gee_client.get_image_collection(
            request.satellite,
            request.date,
            request.date
        )
        
        # Get image and calculate NDVI
        image = collection.first()
        if not image:
            raise HTTPException(404, f"No satellite imagery found for date {request.date}")
        
        ndvi = gee_client.calculate_ndvi(image)
        
        # Sample at point
        sampled = ndvi.sampleRectangle(region=point.buffer(request.buffer_meters))
        
        # Extract value
        try:
            ndvi_value = float(sampled.getInfo()['properties']['NDVI'])
        except:
            # Fallback to reducer
            ndvi_value = ndvi.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=point.buffer(request.buffer_meters),
                scale=10,
                bestEffort=True
            ).get('NDVI').getInfo()
        
        # Classification
        if ndvi_value < 0:
            classification = "water"
        elif ndvi_value < settings.ndvi_threshold_normal:
            classification = "sparse_vegetation"
        elif ndvi_value < settings.ndvi_threshold_dense:
            classification = "moderate_vegetation"
        else:
            classification = "dense_vegetation"
        
        return {
            "ndvi": round(ndvi_value, 4),
            "classification": classification,
            "location": {"lat": request.location.lat, "lon": request.location.lon},
            "date": request.date,
            "satellite": request.satellite
        }
    
    async def detect_change(self, request: ChangeDetectionRequest) -> ChangeDetectionResult:
        """Detect environmental change between two time periods"""
        gee_client.initialize()
        
        # Create region geometry
        region = ee.Geometry.Rectangle([
            request.region.min_lon, request.region.min_lat,
            request.region.max_lon, request.region.max_lat
        ])
        
        # Get images for both periods
        collection = gee_client.get_image_collection(
            "sentinel",
            request.time_range.start_date,
            request.time_range.end_date
        ).filterBounds(region)
        
        # Split into before/after
        mid_date = (datetime.fromisoformat(request.time_range.start_date) + 
                   (datetime.fromisoformat(request.time_range.end_date) - 
                    datetime.fromisoformat(request.time_range.start_date)) / 2)
        mid_date_str = mid_date.strftime("%Y-%m-%d")
        
        before_collection = collection.filterDate(request.time_range.start_date, mid_date_str)
        after_collection = collection.filterDate(mid_date_str, request.time_range.end_date)
        
        # Calculate median composites
        before_image = before_collection.median()
        after_image = after_collection.median()
        
        # Calculate indices
        if request.index == "ndvi":
            before_idx = gee_client.calculate_ndvi(before_image)
            after_idx = gee_client.calculate_ndvi(after_image)
        elif request.index == "ndwi":
            before_idx = gee_client.calculate_ndwi(before_image)
            after_idx = gee_client.calculate_ndwi(after_image)
        else:
            before_idx = gee_client.calculate_ndvi(before_image)
            after_idx = gee_client.calculate_ndvi(after_image)
        
        # Calculate difference
        difference = after_idx.subtract(before_idx).abs()
        
        # Apply threshold to identify change
        change_mask = difference.gt(request.threshold)
        
        # Calculate change statistics
        stats = change_mask.reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=region,
            scale=10,
            bestEffort=True,
            maxPixels=1e9
        )
        
        total_pixels = stats.get('sum').getInfo()
        
        # Approximate area (each 10m pixel = 0.01 hectare)
        total_change_ha = total_pixels * 0.01 if total_pixels else 0
        total_area_ha = request.region.area_hectares
        percent_change = (total_change_ha / total_area_ha * 100) if total_area_ha > 0 else 0
        
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
        recommendations = self._generate_change_recommendations(
            request.index, severity, percent_change
        )
        
        return ChangeDetectionResult(
            total_change_ha=round(total_change_ha, 2),
            percent_change=round(percent_change, 1),
            change_map=None,  # Would generate actual map in production
            severity=severity,
            recommendations=recommendations
        )
    
    def _generate_change_recommendations(self, index: str, severity: str, percent: float) -> List[str]:
        """Generate actionable recommendations based on change detection"""
        recommendations = []
        
        if index == "ndvi":
            if severity == "critical":
                recommendations.append("Immediate intervention required - severe vegetation loss detected")
                recommendations.append("Schedule field verification and assess erosion risk")
            elif severity == "high":
                recommendations.append("Investigate causes of vegetation decline (drought, fire, deforestation)")
            else:
                recommendations.append("Continue monitoring - implement bi-weekly NDVI tracking")
        
        if index == "ndwi":
            if percent > 20:
                recommendations.append("Water body shrinkage detected - conduct hydrological assessment")
            else:
                recommendations.append("Water levels stable - maintain regular monitoring")
        
        return recommendations
    
    async def analyze_vegetation_health(self, request: VegetationHealthRequest) -> dict:
        """Comprehensive vegetation health analysis"""
        gee_client.initialize()
        
        point = ee.Geometry.Point([request.location.lon, request.location.lat])
        results = {}
        
        for metric in request.metrics:
            values = await self._get_time_series_metric(
                point, metric, request.time_range.start_date, request.time_range.end_date
            )
            results[metric] = values
        
        # Calculate trend
        ndvi_trend = self._calculate_trend(results.get("ndvi", []))
        
        return {
            "location": {"lat": request.location.lat, "lon": request.location.lon},
            "time_range": request.time_range.dict(),
            "metrics": results,
            "trend_analysis": ndvi_trend,
            "overall_health": self._assess_vegetation_health(results)
        }
    
    async def _get_time_series_metric(self, point: ee.Geometry, metric: str, start_date: str, end_date: str) -> List[dict]:
        """Extract time series for a specific metric"""
        collection = gee_client.get_image_collection("sentinel", start_date, end_date)
        
        if metric == "ndvi":
            compute_func = gee_client.calculate_ndvi
        elif metric == "evi":
            compute_func = gee_client.calculate_evi
        elif metric == "msavi2":
            compute_func = gee_client.calculate_msavi2
        elif metric == "ndmi":
            compute_func = gee_client.calculate_ndmi
        else:
            compute_func = gee_client.calculate_ndvi
        
        values = []
        image_list = collection.toList(collection.size())
        size = collection.size().getInfo()
        
        for i in range(min(size, 50)):  # Limit to 50 images
            image = ee.Image(image_list.get(i))
            date = image.date().format().getInfo()
            idx = compute_func(image)
            
            value = idx.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=point.buffer(100),
                scale=10,
                bestEffort=True
            ).get(metric.upper()).getInfo()
            
            if value:
                values.append({
                    "date": date[:10],
                    "value": round(float(value), 4)
                })
        
        return values
    
    def _calculate_trend(self, values: List[dict]) -> dict:
        """Calculate trend direction and magnitude"""
        if len(values) < 3:
            return {"direction": "insufficient_data", "percent": 0}
        
        y = [v["value"] for v in values]
        x = list(range(len(y)))
        
        # Simple linear regression
        n = len(x)
        x_mean = sum(x) / n
        y_mean = sum(y) / n
        
        numerator = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
        
        slope = numerator / denominator if denominator != 0 else 0
        percent_change = (slope * n) / y_mean * 100 if y_mean > 0 else 0
        
        if slope > 0.005:
            direction = "increasing"
        elif slope < -0.005:
            direction = "decreasing"
        else:
            direction = "stable"
        
        return {
            "direction": direction,
            "percent": round(abs(percent_change), 1),
            "slope": round(slope, 5)
        }
    
    def _assess_vegetation_health(self, results: dict) -> str:
        """Assess overall vegetation health from multiple metrics"""
        ndvi_values = [v["value"] for v in results.get("ndvi", [])]
        if not ndvi_values:
            return "unknown"
        
        avg_ndvi = sum(ndvi_values) / len(ndvi_values)
        
        if avg_ndvi > 0.6:
            return "excellent"
        elif avg_ndvi > 0.4:
            return "good"
        elif avg_ndvi > 0.2:
            return "moderate"
        else:
            return "poor"
    
    async def assess_wildfire_risk(self, request: WildfireRiskRequest) -> dict:
        """Assess wildfire risk based on vegetation moisture and weather"""
        gee_client.initialize()
        
        region = ee.Geometry.Rectangle([
            request.region.min_lon, request.region.min_lat,
            request.region.max_lon, request.region.max_lat
        ])
        
        # Get current vegetation moisture (NDMI)
        collection = gee_client.get_image_collection("sentinel", request.date, request.date)
        image = collection.first()
        
        if not image:
            raise HTTPException(404, "No imagery available for risk assessment")
        
        ndmi = gee_client.calculate_ndmi(image)
        
        # Sample moisture across region
        moisture = ndmi.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=region,
            scale=500,
            bestEffort=True
        ).get('NDMI').getInfo()
        
        # Calculate risk score (0-100)
        # Lower NDMI = drier = higher risk
        if moisture < -0.2:
            risk_score = 90
        elif moisture < -0.1:
            risk_score = 70
        elif moisture < 0:
            risk_score = 50
        elif moisture < 0.1:
            risk_score = 30
        else:
            risk_score = 10
        
        # Determine risk level
        if risk_score >= 70:
            risk_level = "high"
        elif risk_score >= 40:
            risk_level = "medium"
        else:
            risk_level = "low"
        
        recommendations = []
        if risk_level == "high":
            recommendations.append("Fire weather watch in effect - restrict outdoor burning")
            recommendations.append("Activate monitoring protocols and alert response teams")
        elif risk_level == "medium":
            recommendations.append("Monitor conditions closely - prepare response resources")
        else:
            recommendations.append("Normal conditions - maintain standard monitoring")
        
        return {
            "risk_level": risk_level,
            "risk_score": risk_score,
            "moisture_index": round(float(moisture), 4) if moisture else None,
            "date": request.date,
            "recommendations": recommendations,
            "include_historical": request.include_historical
        }
    
    async def analyze_water_quality(self, request: WaterQualityRequest) -> dict:
        """Analyze water quality parameters using satellite imagery"""
        gee_client.initialize()
        
        region = ee.Geometry.Rectangle([
            request.region.min_lon, request.region.min_lat,
            request.region.max_lon, request.region.max_lat
        ])
        
        collection = gee_client.get_image_collection("sentinel", request.date, request.date)
        image = collection.first()
        
        if not image:
            raise HTTPException(404, "No imagery available for water quality analysis")
        
        results = {}
        
        # Calculate various water quality indicators
        if "chlorophyll" in request.parameters:
            # Chlorophyll-a estimation using red-edge bands
            red_edge = image.select('B5')
            red = image.select('B4')
            chlorophyll_ratio = red_edge.divide(red)
            chlorophyll = chlorophyll_ratio.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=region,
                scale=20,
                bestEffort=True
            ).get('B5').getInfo()
            results["chlorophyll_estimate"] = round(float(chlorophyll) * 10, 2) if chlorophyll else None
        
        if "turbidity" in request.parameters:
            # Turbidity proxy using red band
            red = image.select('B4')
            turbidity = red.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=region,
                scale=10,
                bestEffort=True
            ).get('B4').getInfo()
            results["turbidity_index"] = round(float(turbidity), 4) if turbidity else None
        
        if "sst" in request.parameters:
            # Sea Surface Temperature (requires thermal bands)
            try:
                thermal = image.select('B10')  # Sentinel-2 has no thermal band
                sst = thermal.reduceRegion(
                    reducer=ee.Reducer.mean(),
                    geometry=region,
                    scale=60,
                    bestEffort=True
                ).get('B10').getInfo()
                results["surface_temperature_k"] = round(float(sst), 2) if sst else None
            except:
                results["surface_temperature_k"] = None
        
        return {
            "region": request.region.dict(),
            "date": request.date,
            "parameters": results,
            "quality_status": self._assess_water_quality(results)
        }
    
    def _assess_water_quality(self, results: dict) -> str:
        """Assess water quality from parameters"""
        turbidity = results.get("turbidity_index")
        if turbidity:
            if turbidity < 0.05:
                return "excellent"
            elif turbidity < 0.1:
                return "good"
            elif turbidity < 0.2:
                return "fair"
            else:
                return "poor"
        return "unknown"
    
    async def forecast_time_series(self, request: TimeSeriesAnalysisRequest) -> TimeSeriesResponse:
        """Forecast environmental metrics using time series analysis"""
        gee_client.initialize()
        
        point = ee.Geometry.Point([request.location.lon, request.location.lat])
        
        # Get historical data
        historical = await self._get_time_series_metric(
            point,
            request.metric,
            request.time_range.start_date,
            request.time_range.end_date,
            interval_days=request.interval_days
        )
        
        # Calculate trend
        trend_info = self._calculate_trend(historical)
        
        # Simple forecast (linear extrapolation)
        forecast = []
        if len(historical) >= 3 and request.forecast_days > 0:
            values = [h["value"] for h in historical]
            x = list(range(len(values)))
            
            # Linear regression
            n = len(x)
            x_mean = sum(x) / n
            y_mean = sum(values) / n
            
            numerator = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
            denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
            slope = numerator / denominator if denominator != 0 else 0
            
            # Generate forecast points
            last_date = datetime.fromisoformat(historical[-1]["date"])
            for days in range(1, request.forecast_days + 1, request.interval_days):
                forecast_date = last_date + timedelta(days=days)
                forecast_value = max(0, min(1, y_mean + slope * (n + days)))
                forecast.append(TimeSeriesPoint(
                    date=forecast_date.strftime("%Y-%m-%d"),
                    value=round(forecast_value, 4),
                    quality_flag="forecast"
                ))
        
        # Convert historical to TimeSeriesPoint objects
        historical_points = [
            TimeSeriesPoint(date=h["date"], value=h["value"])
            for h in historical
        ]
        
        return TimeSeriesResponse(
            metric=request.metric,
            values=historical_points,
            trend=trend_info["direction"],
            trend_pct=trend_info["percent"],
            forecast=forecast if forecast else None
        )

geo_service = GeospatialAnalysisService()

# ============================================================
# BIGQUERY INTEGRATION FOR GEOSPATIAL ANALYTICS
# ============================================================

class BigQueryGeospatialPipeline:
    """BigQuery integration for large-scale geospatial analytics"""
    
    def __init__(self):
        self.client = None
        self._initialize()
    
    def _initialize(self):
        """Initialize BigQuery client"""
        try:
            from google.cloud import bigquery
            self.client = bigquery.Client(project=settings.gcp_project_id)
            logger.info("BigQuery client initialized")
        except Exception as e:
            logger.warning(f"BigQuery initialization failed: {e}")
    
    async def store_analysis_result(self, analysis_id: str, result: dict, table: str = "geospatial_analyses"):
        """Store analysis result in BigQuery"""
        if not self.client:
            return
        
        try:
            dataset_ref = self.client.dataset(settings.bigquery_dataset)
            table_ref = dataset_ref.table(table)
            
            rows = [{
                "analysis_id": analysis_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "result_json": json.dumps(result),
                "analysis_type": result.get("type", "unknown")
            }]
            
            errors = self.client.insert_rows_json(table_ref, rows)
            if errors:
                logger.warning(f"BigQuery insert errors: {errors}")
        except Exception as e:
            logger.warning(f"BigQuery storage failed: {e}")
    
    async def get_historical_trends(self, metric: str, region: str, days: int = 90) -> List[dict]:
        """Query historical trends from BigQuery"""
        if not self.client:
            return []
        
        try:
            query = f"""
            SELECT 
                DATE(timestamp) as date,
                AVG(CAST(JSON_EXTRACT(result_json, '$.{metric}') AS FLOAT64)) as avg_value
            FROM `{settings.gcp_project_id}.{settings.bigquery_dataset}.geospatial_analyses`
            WHERE 
                JSON_EXTRACT(result_json, '$.region') = '{region}'
                AND timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
            GROUP BY DATE(timestamp)
            ORDER BY date ASC
            """
            
            results = self.client.query(query).result()
            return [{"date": row.date.isoformat(), "value": row.avg_value} for row in results]
        except Exception as e:
            logger.warning(f"BigQuery query failed: {e}")
            return []

bq_pipeline = BigQueryGeospatialPipeline()

# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title=settings.app_name,
    description="AI-Enhanced Geospatial Intelligence for Environmental Monitoring using Google Earth Engine",
    version=settings.app_version,
    docs_url="/docs",
    redoc_url="/redoc"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://geo.arybit.co.ke",
        "https://arybit.co.ke",
        "http://localhost:3000",
        "http://localhost:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Compression middleware
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Prometheus metrics
Instrumentator().instrument(app).expose(app)

# ============================================================
# HEALTH AND READINESS ENDPOINTS
# ============================================================

@app.on_event("startup")
async def startup_event():
    """Initialize services on startup"""
    logger.info(f"Starting {settings.app_name} v{settings.app_version}")
    
    # Initialize Earth Engine
    try:
        gee_client.initialize()
        logger.info("Earth Engine ready")
    except Exception as e:
        logger.error(f"Earth Engine initialization failed: {e}")
    
    # Initialize Redis for caching
    global redis_client
    redis_client = await init_redis()
    
    logger.info("Geospatial intelligence platform ready")

@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.app_version,
        "gee_ready": gee_client.is_ready,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

@app.get("/ready", tags=["Health"])
async def readiness_check():
    """Readiness probe for orchestration"""
    if not gee_client.is_ready:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "Earth Engine not initialized"}
        )
    
    return {"status": "ready", "timestamp": datetime.now(timezone.utc).isoformat()}

# ============================================================
# GEOSPATIAL INTELLIGENCE ENDPOINTS
# ============================================================

@app.post("/api/ndvi", tags=["Geospatial Analysis"])
async def get_ndvi(request: NDVIRequest):
    """
    Calculate NDVI (Normalized Difference Vegetation Index) for a location
    
    NDVI values range from -1 to 1:
    - < 0: Water
    - 0 - 0.2: Barren/Urban
    - 0.2 - 0.4: Sparse vegetation
    - 0.4 - 0.6: Moderate vegetation
    - > 0.6: Dense vegetation
    """
    try:
        result = await geo_service.get_ndvi(request)
        await bq_pipeline.store_analysis_result(
            str(uuid.uuid4()),
            {**result, "type": "ndvi"}
        )
        return GeospatialAnalysisResponse(
            success=True,
            data=result,
            metadata={"model": "NDVI", "satellite": request.satellite},
            timestamp=datetime.now(timezone.utc).isoformat()
        )
    except Exception as e:
        logger.error(f"NDVI calculation failed: {e}")
        raise HTTPException(500, f"NDVI analysis failed: {str(e)}")

@app.post("/api/change-detection", tags=["Geospatial Analysis"])
async def detect_change(request: ChangeDetectionRequest):
    """
    Detect environmental change between two time periods
    
    Supports multiple indices:
    - ndvi: Vegetation change detection
    - ndwi: Water body change detection
    - ndbi: Built-up area change detection
    """
    try:
        result = await geo_service.detect_change(request)
        await bq_pipeline.store_analysis_result(
            str(uuid.uuid4()),
            {"type": "change_detection", **result.dict()}
        )
        return GeospatialAnalysisResponse(
            success=True,
            data=result.dict(),
            metadata={"algorithm": request.algorithm, "index": request.index},
            timestamp=datetime.now(timezone.utc).isoformat()
        )
    except Exception as e:
        logger.error(f"Change detection failed: {e}")
        raise HTTPException(500, f"Change detection failed: {str(e)}")

@app.post("/api/vegetation-health", tags=["Environmental Monitoring"])
async def analyze_vegetation_health(request: VegetationHealthRequest):
    """
    Comprehensive vegetation health analysis using multiple spectral indices
    
    Metrics available:
    - ndvi: General vegetation health
    - evi: Enhanced vegetation index (reduced atmospheric effects)
    - msavi2: Modified soil-adjusted vegetation index
    - ndmi: Normalized difference moisture index
    """
    try:
        result = await geo_service.analyze_vegetation_health(request)
        await bq_pipeline.store_analysis_result(
            str(uuid.uuid4()),
            {"type": "vegetation_health", **result}
        )
        return GeospatialAnalysisResponse(
            success=True,
            data=result,
            metadata={"metrics": request.metrics},
            timestamp=datetime.now(timezone.utc).isoformat()
        )
    except Exception as e:
        logger.error(f"Vegetation health analysis failed: {e}")
        raise HTTPException(500, f"Vegetation analysis failed: {str(e)}")

@app.post("/api/wildfire-risk", tags=["Environmental Monitoring"])
async def assess_wildfire_risk(request: WildfireRiskRequest):
    """
    Assess wildfire risk based on vegetation moisture and historical patterns
    
    Risk levels:
    - low: Normal conditions
    - medium: Elevated risk - monitor closely
    - high: Severe risk - active monitoring required
    """
    try:
        result = await geo_service.assess_wildfire_risk(request)
        await bq_pipeline.store_analysis_result(
            str(uuid.uuid4()),
            {"type": "wildfire_risk", **result}
        )
        return GeospatialAnalysisResponse(
            success=True,
            data=result,
            metadata={"include_historical": request.include_historical},
            timestamp=datetime.now(timezone.utc).isoformat()
        )
    except Exception as e:
        logger.error(f"Wildfire risk assessment failed: {e}")
        raise HTTPException(500, f"Risk assessment failed: {str(e)}")

@app.post("/api/water-quality", tags=["Environmental Monitoring"])
async def analyze_water_quality(request: WaterQualityRequest):
    """
    Analyze water quality parameters using satellite imagery
    
    Parameters available:
    - chlorophyll: Chlorophyll-a concentration proxy
    - turbidity: Water turbidity index
    - sst: Sea surface temperature (if available)
    """
    try:
        result = await geo_service.analyze_water_quality(request)
        await bq_pipeline.store_analysis_result(
            str(uuid.uuid4()),
            {"type": "water_quality", **result}
        )
        return GeospatialAnalysisResponse(
            success=True,
            data=result,
            metadata={"parameters": request.parameters},
            timestamp=datetime.now(timezone.utc).isoformat()
        )
    except Exception as e:
        logger.error(f"Water quality analysis failed: {e}")
        raise HTTPException(500, f"Water quality analysis failed: {str(e)}")

@app.post("/api/time-series/forecast", tags=["Time Series Analysis"])
async def forecast_time_series(request: TimeSeriesAnalysisRequest):
    """
    Forecast environmental metrics using time series analysis
    
    Uses historical satellite data to predict future trends
    Supports various metrics (ndvi, evi, ndmi, etc.)
    """
    try:
        result = await geo_service.forecast_time_series(request)
        await bq_pipeline.store_analysis_result(
            str(uuid.uuid4()),
            {"type": "time_series_forecast", "metric": request.metric, "forecast_days": request.forecast_days}
        )
        return result
    except Exception as e:
        logger.error(f"Time series forecast failed: {e}")
        raise HTTPException(500, f"Forecast failed: {str(e)}")

@app.get("/api/satellites", tags=["Satellite Data"])
async def list_satellites():
    """List available satellite data sources"""
    return {
        "satellites": [
            {
                "name": "sentinel-2",
                "provider": "ESA",
                "resolution": "10m",
                "bands": ["B2", "B3", "B4", "B8", "B11", "B12"],
                "revisit_days": 5,
                "applications": ["vegetation", "water", "land_cover"]
            },
            {
                "name": "landsat-8",
                "provider": "NASA/USGS",
                "resolution": "30m",
                "bands": ["Coastal", "Blue", "Green", "Red", "NIR", "SWIR1", "SWIR2", "Thermal"],
                "revisit_days": 16,
                "applications": ["land_use", "thermal", "change_detection"]
            },
            {
                "name": "modis",
                "provider": "NASA",
                "resolution": "250m",
                "bands": ["Red", "NIR", "Blue", "Green", "SWIR"],
                "revisit_days": 1,
                "applications": ["large_scale", "daily_monitoring", "fire_detection"]
            }
        ]
    }

@app.get("/api/indices", tags="Geospatial Analysis")
async def list_spectral_indices():
    """List available spectral indices for analysis"""
    return {
        "indices": [
            {
                "name": "NDVI",
                "full_name": "Normalized Difference Vegetation Index",
                "formula": "(NIR - Red) / (NIR + Red)",
                "applications": ["vegetation_health", "crop_monitoring", "deforestation"]
            },
            {
                "name": "EVI",
                "full_name": "Enhanced Vegetation Index",
                "formula": "2.5 * (NIR - Red) / (NIR + 6*Red - 7.5*Blue + 1)",
                "applications": ["dense_vegetation", "atmospheric_correction"]
            },
            {
                "name": "NDWI",
                "full_name": "Normalized Difference Water Index",
                "formula": "(Green - NIR) / (Green + NIR)",
                "applications": ["water_body_detection", "flood_mapping"]
            },
            {
                "name": "NDMI",
                "full_name": "Normalized Difference Moisture Index",
                "formula": "(NIR - SWIR) / (NIR + SWIR)",
                "applications": ["wildfire_risk", "drought_monitoring"]
            },
            {
                "name": "MSAVI2",
                "full_name": "Modified Soil Adjusted Vegetation Index 2",
                "formula": "(2*NIR + 1 - sqrt((2*NIR+1)^2 - 8*(NIR - Red))) / 2",
                "applications": ["arid_regions", "soil_background_correction"]
            }
        ]
    }

@app.post("/api/export/geotiff", tags=["Export"])
async def export_geotiff(
    region: BoundingBox,
    start_date: str = Form(...),
    end_date: str = Form(...),
    index: str = Form("ndvi")
):
    """Export analysis as GeoTIFF for external processing"""
    gee_client.initialize()
    
    # Create region geometry
    geometry = ee.Geometry.Rectangle([
        region.min_lon, region.min_lat,
        region.max_lon, region.max_lat
    ])
    
    # Get image collection and compute index
    collection = gee_client.get_image_collection("sentinel", start_date, end_date)
    image = collection.median()
    
    if index == "ndvi":
        result_image = gee_client.calculate_ndvi(image)
    elif index == "ndwi":
        result_image = gee_client.calculate_ndwi(image)
    else:
        result_image = gee_client.calculate_ndvi(image)
    
    # Export configuration
    export_config = {
        "scale": 10,
        "region": geometry,
        "crs": "EPSG:4326",
        "maxPixels": 1e9
    }
    
    return {
        "success": True,
        "message": "Export initiated",
        "export_config": export_config,
        "estimated_size_mb": round(region.area_hectares * 0.01, 2)  # Rough estimate
    }

# ============================================================
# REDIS CACHING SETUP
# ============================================================

redis_client = None

async def init_redis():
    """Initialize Redis connection for caching"""
    try:
        redis_url = f"redis://{settings.redis_host}:{settings.redis_port}"
        if settings.redis_password:
            redis_url = f"redis://:{settings.redis_password}@{settings.redis_host}:{settings.redis_port}"
        
        client = aioredis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5
        )
        await client.ping()
        logger.info("Redis connected successfully")
        return client
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}. Running without cache.")
        return None

# ============================================================
# MAIN ENTRY POINT
# ============================================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    
    logger.info(f"Starting {settings.app_name} on {host}:{port}")
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=False,
        log_level=settings.log_level.lower()
    )

# ============================================================
# REDIS CONFIGURATION - SHARED UTILITIES
# ============================================================
REDIS_URL = settings.redis_url or ""

MAX_CONVERSATIONS = 10000
MAX_MESSAGES_PER_CONVO = 50
CONVERSATION_TTL_SECONDS = 3600
REDIS_TTL = CONVERSATION_TTL_SECONDS

def build_redis_url() -> str:
    """Build a Redis URL from environment or settings without mutating globals."""
    password = os.getenv("REDIS_PASSWORD") or os.getenv("REDIS_PASS", "")
    host = getattr(settings, "redis_host", "127.0.0.1")
    port = getattr(settings, "redis_port", 6379)

    if password:
        return f"redis://:{quote(str(password), safe='')}@{host}:{port}/0"

    env_url = os.getenv("REDIS_URL", "").strip()
    if env_url:
        return env_url

    return f"redis://{host}:{port}/0"

class RedisLock:
    """Thread-safe + process-safe distributed lock using Redis."""
    def __init__(self, redis_client, lock_key: str, ttl: int = 45):
        self.redis = redis_client
        self.lock_key = f"lock:{lock_key}"
        self.ttl = ttl
        self.token = str(uuid.uuid4())
        self.acquired = False

    async def acquire(self, blocking: bool = True, timeout: float = 15.0) -> bool:
        if not self.redis:
            self.acquired = True
            return True
        start = time.time()
        logger.debug(f"REDIS | LOCK | {self.lock_key} | Requesting acquire (blocking={blocking})")
        while True:
            try:
                acquired = await asyncio.wait_for(
                    self.redis.set(self.lock_key, self.token, nx=True, px=self.ttl * 1000),
                    timeout=min(2.0, timeout),
                )
            except asyncio.TimeoutError:
                logger.warning(f"REDIS | LOCK | {self.lock_key} | Redis set timed out")
                return False
            except Exception as e:
                logger.warning(f"REDIS | LOCK | {self.lock_key} | Acquire error: {e}")
                return False

            if acquired:
                logger.debug(f"REDIS | LOCK | {self.lock_key} | Acquired by token {self.token[:8]}")
                self.acquired = True
                return True
            if not blocking or time.time() - start > timeout:
                logger.warning(f"REDIS | LOCK | {self.lock_key} | Failed to acquire after {time.time() - start:.2f}s")
                return False
            await asyncio.sleep(0.05 + random.random() * 0.1)

    async def release(self):
        if not self.acquired or not self.redis:
            return
        try:
            script = """
            if redis.call("GET", KEYS[1]) == ARGV[1] then
                return redis.call("DEL", KEYS[1])
            else
                return 0
            end
            """
            await self.redis.eval(script, 1, self.lock_key, self.token)
            self.acquired = False
            logger.debug(f"REDIS | LOCK | {self.lock_key} | Released")
        except Exception as e:
            logger.warning(f"REDIS LOCK | {self.lock_key} | Release error: {e}")
    async def __aenter__(self): await self.acquire(); return self
    async def __aexit__(self, exc_type, exc_val, exc_tb): await self.release()

def safe_send_task(*args, **kwargs):
    """Nuclear-safe dispatch"""
    CELERY_BROKER_FINAL = get_stable_redis_url()
    celery_app.conf.broker_url = CELERY_BROKER_FINAL
    celery_app.conf.result_backend = CELERY_BROKER_FINAL
    return celery_app.send_task(*args, **kwargs)

def _push_document_dead_letter(task_id: str, request_id: str, filename: str, org_id: int, doc_id: int, error: str) -> None:
    """Store permanently failed document tasks in Redis for manual replay."""
    try:
        import redis as sync_redis
        payload = {
            "task_id": task_id,
            "request_id": request_id,
            "filename": filename,
            "org_id": org_id,
            "doc_id": doc_id,
            "error": error,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        r = sync_redis.from_url(build_redis_url(), decode_responses=True)
        r.lpush("dead_letter:documents", json.dumps(payload)) # type: ignore
        r.expire("dead_letter:documents", settings.dead_letter_ttl_seconds) # type: ignore
        r.close()
        logger.critical(f"{request_id} | DOCUMENT | Dead letter queued: {payload}")
    except Exception as dl_err:
        logger.error(f"{request_id} | DOCUMENT | Dead letter write failed: {dl_err}")

@celery_app.task(name="maintenance_dlq_audit")
def maintenance_dlq_audit():
    """Maintenance task to audit the Dead Letter Queue for failed document processing."""
    try:
        import redis as sync_redis
        r = sync_redis.from_url(build_redis_url(), decode_responses=True)
        q_len = r.llen("dead_letter:documents")
        if q_len > 0:
            logger.critical(f"SYSTEM | MAINTENANCE | DLQ Audit | {q_len} documents pending manual repair in dead_letter:documents")
        r.close()
    except Exception as e:
        logger.error(f"SYSTEM | MAINTENANCE | DLQ Audit failed: {e}")

# Celery task definition
@celery_app.task(name="process_legal_doc", bind=True, max_retries=3, default_retry_delay=60, acks_late=True)
def process_document_task(self, file_payload: str, filename: str, content_type: str, org_id: int, doc_id: int, request_id: str):
    """
    Background task for document indexing. 
    Fidelity Fix: Implements page-number mapping.
    """
    file_bytes = _file_payload_to_bytes(file_payload, request_id)
    celery_task_id = self.request.id
    logger.info(f"{request_id} | CELERY | Task {celery_task_id} starting document processing for {filename} (doc_id={doc_id})")

    async def _run_async_worker_logic():
        q_client = None
        o_client = None
        r_client = None
        try:
            logger.info(f"{request_id} | CELERY | Task {celery_task_id} worker logic starting")
            q_client = AsyncQdrantClient(url=QDRANT_HOST)
            o_client = httpx.AsyncClient(base_url=OLLAMA_HOST, timeout=REQUEST_TIMEOUT)
            
            redis_url = build_redis_url()
            r_client = aioredis.from_url(
                redis_url, 
                decode_responses=True,
                socket_connect_timeout=8,
                socket_timeout=8
            )
            await _index_document_internal(file_bytes, filename, content_type, org_id, doc_id, q_client, o_client, r_client, request_id, vertical="core")
        finally:
            if q_client: await q_client.close()
            if o_client: await o_client.aclose()
            if r_client: await r_client.aclose()

    try:
        asyncio.run(asyncio.wait_for(_run_async_worker_logic(), timeout=DOCUMENT_PROCESSING_TIMEOUT))
        return {"status": "success", "doc_id": doc_id}
    except Exception as e:
        from celery.exceptions import Retry
        if isinstance(e, Retry):
            raise
        logger.error(f"{request_id} | CELERY | Task {celery_task_id} failed: {e}", exc_info=True)
        if self.request.retries >= self.max_retries:
            _push_document_dead_letter(celery_task_id, request_id, filename, org_id, doc_id, str(e))
            raise
        raise self.retry(exc=e)

@celery_app.task(name="process_aarab_document", bind=True, max_retries=3, default_retry_delay=60, acks_late=True)
def process_aarab_document_task(
    self,
    file_payload: str,
    filename: str,
    content_type: str,
    org_id: int,
    doc_id: int,
    request_id: str,
    agent_type: str,
    routing_info_json: Optional[str] = None,
):
    """Background indexing for AARAB vertical documents with agent attribution."""
    file_bytes = _file_payload_to_bytes(file_payload, request_id)
    celery_task_id = self.request.id
    routing_info = json.loads(routing_info_json) if routing_info_json else None
    logger.info(
        f"{request_id} | AARAB | Celery task {celery_task_id} | agent={agent_type} | file={filename}"
    )

    async def _run_async_worker_logic():
        q_client = None
        o_client = None
        r_client = None
        try:
            q_client = AsyncQdrantClient(url=QDRANT_HOST)
            o_client = httpx.AsyncClient(base_url=OLLAMA_HOST, timeout=REQUEST_TIMEOUT)
            redis_url = build_redis_url()
            r_client = aioredis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=8,
                socket_timeout=8,
            )
            await _index_document_internal(
                file_bytes,
                filename,
                content_type,
                org_id,
                doc_id,
                q_client,
                o_client,
                r_client,
                f"{request_id}-aarab-{agent_type}",
                vertical="aarab",
            )
            if routing_info:
                logger.info(
                    f"{request_id} | AARAB | Indexed with agent={agent_type} | "
                    f"confidence={routing_info.get('confidence', 0)}"
                )
        finally:
            if q_client:
                await q_client.close()
            if o_client:
                await o_client.aclose()
            if r_client:
                await r_client.aclose()

    try:
        asyncio.run(asyncio.wait_for(_run_async_worker_logic(), timeout=DOCUMENT_PROCESSING_TIMEOUT))
        return {"status": "success", "doc_id": doc_id, "agent_type": agent_type}
    except Exception as e:
        from celery.exceptions import Retry
        if isinstance(e, Retry):
            raise
        logger.error(f"{request_id} | AARAB | Celery task failed: {e}", exc_info=True)
        if self.request.retries >= self.max_retries:
            _push_document_dead_letter(celery_task_id, request_id, filename, org_id, doc_id, str(e))
            raise
        raise self.retry(exc=e)

def parse_trusted_proxies():
    raw = os.getenv("TRUSTED_PROXIES", "127.0.0.1,::1,10.0.0.0/8")
    networks = []
    for item in raw.split(","):
        try:
            networks.append(ipaddress.ip_network(item.strip()))
        except ValueError:
            continue
    return networks
TRUSTED_NETWORKS = parse_trusted_proxies()

def rate_limit_key(raw: str) -> str:
    """Hash the API key or token for safe storage in memory."""
    return hashlib.sha256(raw.encode()).hexdigest()[:32]

def build_identity_key(user_id: Optional[str], api_key: Optional[str], org_id: Optional[str] = None) -> str:
    """Namespace identities so user IDs and API keys cannot collide."""
    org_prefix = f"org:{org_id}:" if org_id else ""
    if user_id:
        return f"{org_prefix}user:{user_id}"
    if api_key and not api_key.startswith("internal:"): # Don't use internal service names as user keys
        return f"{org_prefix}key:{api_key}"
    return f"{org_prefix}anonymous"

def request_identity_key(request: Request, user: Optional[dict] = None) -> str:
    """Resolve the canonical identity key for request-scoped tracking."""
    user_id = user.get("user_id") if isinstance(user, dict) else None
    # Prioritize legal_organization_id from request.state set during auth
    org_id = getattr(request.state, "legal_organization_id", None)
    if not org_id:
        org_id = user.get("legal_organization_id") if isinstance(user, dict) else request.headers.get("X-Organization-ID")
    api_key = getattr(request.state, "api_key", None)
    return build_identity_key(str(user_id) if user_id else None, api_key, str(org_id) if org_id else None)

def decode_delegated_worker_token(token: str) -> Optional[dict]:
    """Verify a server-minted delegated worker JWT without requiring a browser session row."""
    if not JWT_SECRET:
        logger.error("AUTH | decode_delegated_worker_token failed: JWT_SECRET not configured")
        return None # type: ignore

    try:
        payload = decode_jwt_with_rotation(
            token,
            algorithms=[JWT_ALGORITHM],
            audience="aarab-worker",
            issuer="aarab-api",
        )
    except jwt.PyJWTError as exc: # type: ignore
        logger.warning("AUTH | Delegated worker JWT rejected: %s | SecretLen: %d", exc, len(JWT_SECRET))
        return None

    data = payload.get("data") or {}
    if data.get("delegated") is not True:
        logger.warning("AUTH | Delegated worker JWT rejected: 'delegated' claim missing or false")
        return None

    purpose = str(data.get("purpose") or "")
    if purpose not in {"research_worker", "research_initiation"}:
        logger.warning("AUTH | Delegated worker JWT rejected: unsupported purpose=%s", purpose)
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    roles = data.get("roles")
    if not isinstance(roles, list):
        roles = ["user"]

    return {
        "user": {
            "user_id": str(user_id),
            "username": data.get("username") or f"user_{user_id}",
            "email": data.get("email"),
            "role": "user",
            "roles": roles,
            "scopes": ["chat", "embeddings", "research_worker"],
            "kyc_status": data.get("kyc_status", "verified"),
            "subscription": data.get("subscription") or {"status": "active"},
            "quota_details": data.get("quota_details"),
        },
        "access_token": token,
        "csrf_token": None,
        "delegated": True,
        "worker_purpose": purpose,
    }

# Model-specific concurrency tuning (from settings)
MODEL_CONCURRENCY = {
    "llama3.2:3b": settings.concurrency_llama_3b,
    "llama3.2:1b": settings.concurrency_llama_1b,
    "phi3:mini": settings.concurrency_phi3,
    "nomic-embed-text": settings.concurrency_embedding
}

# Global semaphores for model concurrency control
model_semaphores = {model: asyncio.Semaphore(MODEL_CONCURRENCY.get(model, 2)) for model in list(ALLOWED_MODELS)}

# Limit concurrent batches sent to Ollama to prevent overwhelming the server
embedding_batch_semaphore = asyncio.Semaphore(MAX_CONCURRENT_EMBEDDING_BATCHES)

# Global admission control
# Move definition higher to avoid NameError in lifespan logging
GLOBAL_MAX_CONCURRENT_REQUESTS = settings.global_max_concurrent_requests
global_admission_semaphore = asyncio.Semaphore(GLOBAL_MAX_CONCURRENT_REQUESTS)

# Enterprise Security Configuration
API_KEYS = {k.strip() for k in os.getenv("API_KEYS", "dev-key").split(",") if k.strip()}
RATE_LIMIT = int(os.getenv("RATE_LIMIT", "60"))  # Requests per minute
rate_limit_store = defaultdict(deque)
# Common bot/scanner patterns to reduce log noise (moved to settings)
NOISY_PATHS = {
    "/.env", "/.git", "/wp-admin", "/wp-login.php",
    "/.vscode", "/phpmyadmin", "/xmlrpc.php", "/debug",
    "/wp-json", "/cgi-bin", "/.well-known",
    "/trace.axd", "/%40vite/env", "/.vscode/sftp.json",
    "/debug/default/view", "/actuator", "/vendor",
    "/composer.json", "/package.json", "/.aws", "/.ssh", "/.env" # Added .env
}
BLOCKED_IPS = {ip.strip() for ip in os.getenv("BLOCKED_IPS", "165.227.84.14").split(",") if ip.strip()}
rate_limit_lock = asyncio.Lock()


def resolve_client_ip(request: Request) -> str:
    """Resolve client IP, honoring X-Forwarded-For only behind trusted proxies."""
    client_ip = getattr(request.client, "host", "") or "unknown"
    try: # type: ignore
        addr = ipaddress.ip_address(client_ip)
        is_trusted = any(addr in net for net in TRUSTED_NETWORKS)
    except ValueError:
        is_trusted = False
    if is_trusted:
        xff = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if xff:
            return xff
    return client_ip

STREAM_INACTIVITY_TIMEOUT = settings.stream_inactivity_timeout  # CPU Ollama prefill can exceed 3 min
STREAM_LINE_POLL_SEC = settings.stream_line_poll_sec

def get_stream_timeout(content_length: int, has_file: bool = False) -> float:
    """Calculate dynamic timeout based on payload complexity."""
    if has_file:
        return 1200.0  # 20 minutes for heavy document processing on CPU
    if content_length > 10000:
        return 600.0   # 10 minutes for long research prompts
    if content_length > 2000:
        return 300.0  # 5 minutes
    return STREAM_INACTIVITY_TIMEOUT

MAX_REQUEST_SIZE = settings.max_request_size  # 80MB - supports larger media files

# === FILE SUPPORT CONFIGURATION ===
# Programming / Code Files (very comprehensive)
CODE_EXT = {
    '.py', '.js', '.ts', '.jsx', '.tsx', '.java', '.cpp', '.c', '.cs', '.go', '.rs',
    '.php', '.rb', '.swift', '.kt', '.scala', '.sh', '.bash', '.ps1', '.sql',
    '.html', '.css', '.scss', '.sass', '.less', '.json', '.xml', '.yaml', '.yml',
    '.toml', '.md', '.markdown', '.rst', '.txt', '.ipynb', '.vue', '.svelte',
    '.dart', '.lua', '.r', '.m', '.jl', '.hs', '.erl', '.ex', '.exs', '.clj'
}

# Media Files
IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff', '.svg', '.heic', '.heif'}
VIDEO_EXT = {'.mp4', '.avi', '.mov', '.mkv', '.webm', '.flv', '.wmv', '.m4v', '.mpg', '.mpeg'}
AUDIO_EXT = {'.mp3', '.wav', '.ogg', '.m4a', '.flac', '.aac', '.wma', '.opus'}

# Documents & Others
DOC_EXT = {'.pdf', '.docx', '.doc', '.rtf', '.txt', '.csv', '.xls', '.xlsx', '.ppt', '.pptx'}

ALLOWED_EXT = CODE_EXT | IMAGE_EXT | VIDEO_EXT | AUDIO_EXT | DOC_EXT

ALLOWED_MIME_PREFIXES = {
    'text/', 'application/json', 'application/xml', 'application/yaml',
    'image/', 'video/', 'audio/',
    'application/pdf', 'application/vnd.openxmlformats-officedocument'
}

FILE_MAGIC_SIGNATURES = {
    '.pdf': [b'%PDF'],
    '.png': [b'\x89PNG\r\n\x1a\n'],
    '.jpg': [b'\xff\xd8\xff'],
    '.jpeg': [b'\xff\xd8\xff'],
    '.gif': [b'GIF87a', b'GIF89a'],
    '.docx': [b'PK\x03\x04'],
    '.doc': [b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'],
    '.zip': [b'PK\x03\x04'],
    '.mp4': [b'\x00\x00\x00', b'ftyp'],
    '.mp3': [b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'],
}

EXT_EXPECTED_MIME = {
    '.pdf': ('application/pdf',),
    '.png': ('image/png',),
    '.jpg': ('image/jpeg',),
    '.jpeg': ('image/jpeg',),
    '.gif': ('image/gif',),
    '.docx': (
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/zip',
    ),
    '.doc': ('application/msword',),
}

def validate_upload_triple(file_bytes: bytes, filename: str, content_type: str) -> None:
    """Magic-byte + extension + MIME consistency check for document uploads."""
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file")

    ext = os.path.splitext(filename.lower())[1]
    if ext and ext not in ALLOWED_EXT:
        raise HTTPException(status_code=415, detail=f"Unsupported file extension: {ext}")

    magic_list = FILE_MAGIC_SIGNATURES.get(ext)
    if magic_list:
        if not any(file_bytes.startswith(sig) for sig in magic_list):
            if ext == '.mp4':
                if b'ftyp' not in file_bytes[:32]:
                    raise HTTPException(
                        status_code=415,
                        detail="File content does not match declared type (magic bytes)",
                    )
            else:
                raise HTTPException(
                    status_code=415,
                    detail="File content does not match declared type (magic bytes)",
                )

    expected_mimes = EXT_EXPECTED_MIME.get(ext)
    if expected_mimes and content_type:
        ct = content_type.split(';')[0].strip().lower()
        if ct in ('application/octet-stream', 'binary/octet-stream'):
            return
        if not any(ct == em or ct.startswith(em.split('/')[0] + '/') for em in expected_mimes):
            if not any(ct.startswith(prefix.rstrip('/')) for prefix in ALLOWED_MIME_PREFIXES if '/' in prefix):
                raise HTTPException(
                    status_code=415,
                    detail=f"MIME type '{content_type}' inconsistent with extension '{ext}'",
                )


def validate_upload_file_path(file_path: str, filename: str, content_type: str) -> None:
    """Validate an uploaded file using a filesystem path without loading the full content."""
    if not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail="Uploaded file path missing")

    with open(file_path, 'rb') as f:
        sample = f.read(8192)

    if not sample:
        raise HTTPException(status_code=400, detail="Empty file")

    ext = os.path.splitext(filename.lower())[1]
    if ext and ext not in ALLOWED_EXT:
        raise HTTPException(status_code=415, detail=f"Unsupported file extension: {ext}")

    magic_list = FILE_MAGIC_SIGNATURES.get(ext)
    if magic_list and not any(sample.startswith(sig) for sig in magic_list):
        if ext == '.mp4':
            if b'ftyp' not in sample[:32]:
                raise HTTPException(
                    status_code=415,
                    detail="File content does not match declared type (magic bytes)",
                )
        else:
            raise HTTPException(
                status_code=415,
                detail="File content does not match declared type (magic bytes)",
            )

    expected_mimes = EXT_EXPECTED_MIME.get(ext)
    if expected_mimes and content_type:
        ct = content_type.split(';')[0].strip().lower()
        if ct in ('application/octet-stream', 'binary/octet-stream'):
            return
        if not any(ct == em or ct.startswith(em.split('/')[0] + '/') for em in expected_mimes):
            if not any(ct.startswith(prefix.rstrip('/')) for prefix in ALLOWED_MIME_PREFIXES if '/' in prefix):
                raise HTTPException(
                    status_code=415,
                    detail=f"MIME type '{content_type}' inconsistent with extension '{ext}'",
                )

def _extract_pdf_text_safe(file_bytes: bytes, request_id: str, page_limit: int = 25) -> str:
    """Robust PDF text extraction using PyMuPDF."""
    try:
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            text_pages = []
            num_pages = len(doc)
            for i in range(min(num_pages, page_limit)):
                text_pages.append(doc[i].get_text("text"))
            return "\n".join(text_pages)
    except Exception as e:
        logger.warning(f"{request_id} | PDF extraction failed: {e}")
        return ""


def read_file_head_text(file_path: str, max_bytes: int = 6000) -> str:
    try:
        with open(file_path, 'rb') as f:
            head = f.read(max_bytes)
        return head.decode('utf-8', errors='ignore')
    except Exception:
        return ""


def _extract_pdf_text_safe_from_path(file_path: str, request_id: str, page_limit: int = 25) -> str:
    try:
        with fitz.open(file_path) as doc:
            text_pages = []
            num_pages = len(doc)
            for i in range(min(num_pages, page_limit)):
                text_pages.append(doc[i].get_text("text"))
            return "\n".join(text_pages)
    except Exception as e:
        logger.warning(f"{request_id} | PDF path extraction failed: {e}")
        return ""


async def save_upload_file_to_tempfile(file: UploadFile, max_size: int = 50 * 1024 * 1024) -> tuple[str, int, str]:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename or ".bin")[1] or ".bin", prefix="arybit-upload-", dir=tempfile.gettempdir())
    file_path = temp_file.name
    sha256 = hashlib.sha256()
    total = 0

    try:
        awaitable = getattr(file, 'read', None)
        if awaitable is None or not callable(awaitable):
            raise HTTPException(status_code=500, detail="Upload file object invalid")

        while True:
            chunk = await file.read(8192)
            if not chunk:
                break
            total += len(chunk)
            if total > max_size:
                temp_file.close()
                os.unlink(file_path)
                raise HTTPException(status_code=413, detail="File too large")
            temp_file.write(chunk)
            sha256.update(chunk)
    finally:
        try:
            temp_file.flush()
        except Exception:
            pass
        temp_file.close()

    return file_path, total, sha256.hexdigest()


def _file_payload_to_bytes(file_payload: str, request_id: str) -> bytes:
    if os.path.exists(file_payload):
        with open(file_payload, 'rb') as f:
            data = f.read()
        try:
            os.unlink(file_payload)
        except Exception:
            pass
        return data
    return base64.b64decode(file_payload)


def estimate_tokens(text: str) -> int:
    """Improved lightweight token estimation for English, code, and math prompts."""
    if not text:
        return 0
    text = text.strip()
    if not text: # type: ignore
        return 0
    # Rough approximation: 1 token ≈ 4 chars or 0.75 words
    char_estimate = len(text) // 4
    word_estimate = len(text.split())
    return int(max(char_estimate, word_estimate) * 1.1)  # Add 10% safety buffer

def safe_file_hash(file_path: str, chunk_size: int = 65536) -> str:
    """Calculate SHA-256 hash of file using chunked reading to prevent memory issues."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            sha256.update(chunk)
    return sha256.hexdigest()

# CORE: Cross-Vertical Orchestration Prompts
VERTICAL_PROMPTS = {
    "core": {"system": "You are the Arybit Intelligence CORE, a foundational AI infrastructure designed for orchestration and reasoning across multiple specialized verticals. Provide concise, high-utility analysis."},
    "legal": {"system": "You are a highly specialized legal AI assistant, expert in Kenyan law. Provide concise, accurate, and actionable legal analysis. Cite sources where possible. Maintain client-attorney privilege and confidentiality. Focus on legal risks, compliance, and drafting best practices."},
    "professional_services": { # Updated prompt for Professional Services vertical
        "system": """You are Arybit ProServe — elite strategy & advisory AI.
Expert at:
- Winning RFP responses
- Executive memos
- Proposal structuring
- Scoping & pricing
- Knowledge synthesis

Tone: Professional, confident, commercially astute.
Use structure: headings, bullets, tables.""",
        "temperature": 0.65,
        "max_tokens": 8192
    },
    "fintech": {"system": "You are a financial intelligence assistant. Analyze transactions, identify behavioral patterns, and provide insights into financial health while maintaining strict adherence to financial regulations."},
    "aarab": {
        "system": (
            "You are AARAB (Arybit Autonomous Research Agents), a sovereign AI intelligence platform " # type: ignore
            "built by Arybit Technologies. You are NOT associated with Microsoft, OpenAI, Google, or any " # type: ignore
            "other company. You are an independent, self-hosted AI system operating on Arybit infrastructure. " # type: ignore
            "Your role is to provide research orchestration, multi-source synthesis, source traceability, " # type: ignore
            "contradiction checks, and structured reporting. Always identify yourself as 'AARAB by Arybit'. "
            "Never claim to be developed by Microsoft, OpenAI, or any third party. "
            "Tone: professional, analytical, precise. Answer concisely unless the user asks for depth."
        ),
        "temperature": 0.45,
        "max_tokens": 8192
    }
}

# Vertical-Specific Allowed Model Sets
VERTICAL_MODELS = {
    "professional_services": {"llama3.2:3b", "phi3:mini", "nomic-embed-text"},
    "legal": {"llama3.2:3b", "llama3.2:1b", "nomic-embed-text"},
    "aarab": {"llama3.2:3b", "phi3:mini", "nomic-embed-text"},
    "core": ALLOWED_MODELS # Default to all allowed
}

@lru_cache(maxsize=32)
def get_vertical_config(vertical: str) -> dict:
    """Cached lookup for vertical-specific prompts and configurations."""
    return VERTICAL_PROMPTS.get(vertical, VERTICAL_PROMPTS["core"])

@lru_cache(maxsize=32)
def get_vertical_models(vertical: str) -> Set[str]: # type: ignore
    """Cached lookup for models allowed in a specific vertical."""
    return VERTICAL_MODELS.get(vertical, ALLOWED_MODELS)

GRACE_ALLOWED_MODELS: Set[str] = {"llama3.2:1b", "phi3:mini", "nomic-embed-text"}
GRACE_MAX_DOCUMENTS_PER_DAY = settings.grace_max_documents_per_day
GRACE_MAX_DOC_SIZE_MB = settings.grace_max_doc_size_mb
# ============================================================
# PROMETHEUS METRICS (Safe registration)
# ============================================================
_metric_lock = threading.Lock()

def safe_counter(name: str, documentation: str, labelnames: list = None):
    """Register counter only if it doesn't already exist in the registry."""
    if labelnames is None:
        labelnames = []
    with _metric_lock:
        try:
            return REGISTRY._names_to_collectors[name]
        except KeyError:
            return Counter(name, documentation, labelnames, registry=REGISTRY)

def safe_gauge(name: str, documentation: str, labelnames: list = None):
    """Register gauge only if it doesn't already exist in the registry."""
    if labelnames is None:
        labelnames = []
    with _metric_lock:
        try:
            return REGISTRY._names_to_collectors[name]
        except KeyError:
            return Gauge(name, documentation, labelnames, registry=REGISTRY)

def safe_histogram(name: str, documentation: str, labelnames: list = None, buckets=None):
    """Register histogram only if it doesn't already exist in the registry."""
    if labelnames is None:
        labelnames = []
    if buckets is None:
        buckets = Histogram.DEFAULT_BUCKETS
    with _metric_lock:
        try:
            return REGISTRY._names_to_collectors[name]
        except KeyError:
            return Histogram(name, documentation, labelnames, buckets=buckets, registry=REGISTRY)

kyc_grace_requests = safe_counter(
    "arybit_kyc_grace_requests_total",
    "Number of grace-mode AI requests allowed for KYC-pending users",
    ["path"]
)

business_requests = safe_counter(
    "arybit_business_requests_total",
    "Business requests by vertical and endpoint",
    ["vertical", "endpoint", "status"]
)

document_chunks_indexed = safe_counter(
    "arybit_document_chunks_indexed_total",
    "Total chunks successfully indexed into Qdrant",
    ["org_id"]
)

embedding_batches_processed = safe_counter(
    "arybit_embedding_batches_total",
    "Number of embedding batches processed",
    ["org_id", "batch_size"]
)

qdrant_collection_size = safe_gauge(
    "arybit_qdrant_collection_size",
    "Total points in the Qdrant legal_docs collection",
    ["collection"]
)

kyc_blocked_requests = safe_counter(
    "arybit_kyc_blocked_requests_total",
    "Number of AI requests blocked due to pending KYC",
    ["path", "reason"]
)

auth_mismatch_counter = safe_counter(
    "arybit_auth_binding_mismatches_total",
    "Number of authentication failures likely due to session binding/mismatch",
    ["source"]
)

active_requests_gauge = safe_gauge(
    "arybit_active_requests_total",
    "Current number of active inference requests",
    ["request_type", "model"]
)

model_active_gauge = safe_gauge(
    "arybit_model_active_requests",
    "Current active requests per model",
    ["model"]
)

redis_cb_state_gauge = safe_gauge(
    "arybit_redis_circuit_breaker_state",
    "Current state of the Redis circuit breaker (0=closed, 1=open)"
)

model_concurrency_limit_gauge = safe_gauge(
    "arybit_model_concurrency_limit",
    "Configured maximum concurrency per model",
    ["model"]
)

global_admission_available_gauge = safe_gauge(
    "arybit_global_admission_available_slots",
    "Current number of remaining global admission slots"
)

global_admission_capacity_gauge = safe_gauge(
    "arybit_global_admission_capacity",
    "Configured maximum global admission capacity"
)

kyc_grace_tokens_used_gauge = safe_gauge(
    "arybit_kyc_grace_tokens_used",
    "Total tokens consumed in grace mode today on this gateway instance",
    ["date"]
)

auth_failures_counter = safe_counter(
    "arybit_auth_failures_total",
    "Total number of authentication failures",
    ["reason"]
)

document_queue_depth = safe_gauge(
    "arybit_document_queue_depth",
    "Current number of pending document processing tasks"
)

inference_duration_histogram = safe_histogram(
    "arybit_inference_duration_seconds",
    "End-to-end inference duration by model and request type",
    ["model", "request_type"],
    buckets=[0.5, 1, 2, 5, 10, 30, 60, 120, 300]
)

circuit_breaker_gauge = safe_gauge(
    'arybit_circuit_breaker_state',
    'Circuit breaker state (0=closed, 1=open)',
    ['model']
)

grace_usage_by_date = defaultdict(int)
grace_usage_async_lock = asyncio.Lock()
import threading
grace_usage_sync_lock = threading.Lock()

async def record_grace_usage(tokens_used: int, user_id: Optional[str] = None) -> None:
    """Redis-backed daily grace token counter (multi-worker safe)."""
    if tokens_used <= 0:
        return
    today = datetime.now(timezone.utc).date().isoformat()
    uid = str(user_id or "global") # type: ignore
    key = f"grace:usage:{uid}:{today}"
    if redis_client:
        total = await safe_redis_op(redis_client.incrby(key, tokens_used))
        await safe_redis_op(redis_client.expire(key, 172800))  # 48h
        if total is not None:
            kyc_grace_tokens_used_gauge.labels(date=today).set(int(total))
            return
    async with grace_usage_async_lock:
        grace_usage_by_date[today] += tokens_used
        kyc_grace_tokens_used_gauge.labels(date=today).set(grace_usage_by_date[today])

def set_global_admission_metrics():
    """Reflect current global admission semaphore state in Prometheus."""
    global_admission_available_gauge.set(getattr(global_admission_semaphore, "_value", 0))
    global_admission_capacity_gauge.set(GLOBAL_MAX_CONCURRENT_REQUESTS)

def register_model_limit_metrics():
    """Register configured per-model concurrency limits for dashboards."""
    for model, limit in MODEL_CONCURRENCY.items():
        model_concurrency_limit_gauge.labels(model=model).set(limit)

def record_grace_usage_metric(tokens_used: int, user_id: Optional[str] = None):
    """Track daily grace-mode token consumption (schedules Redis write when available)."""
    if tokens_used <= 0:
        return
    try:
        loop = asyncio.get_running_loop() # type: ignore
        loop.create_task(record_grace_usage(tokens_used, user_id))
    except RuntimeError:
        today = datetime.now(timezone.utc).date().isoformat()
        with grace_usage_sync_lock:
            grace_usage_by_date[today] += tokens_used
            kyc_grace_tokens_used_gauge.labels(date=today).set(grace_usage_by_date[today])

async def enforce_org_document_upload_limit(org_id: int, request_id: str) -> None:
    """Per-organization document upload rate limit (Redis-backed)."""
    if not redis_client or org_id <= 0:
        return
    minute_key = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
    key = f"doc_upload:{org_id}:{minute_key}" # type: ignore
    count = await safe_redis_op(redis_client.incr(key), default=0) # type: ignore
    if count == 1:
        await safe_redis_op(redis_client.expire(key, 60))
    if int(count or 0) > DOC_UPLOADS_PER_MINUTE:
        log_ctx(
            request_id,
            "INGEST | Org upload rate limit exceeded",
            level="warning",
            org_id=org_id,
            count=count,
            limit=DOC_UPLOADS_PER_MINUTE,
        )
        raise HTTPException(
            status_code=429,
            detail={
                "error": "DOCUMENT_UPLOAD_LIMIT_EXCEEDED",
                "message": f"Organization upload limit of {DOC_UPLOADS_PER_MINUTE} documents per minute exceeded.",
                "limit": DOC_UPLOADS_PER_MINUTE,
            },
        )

def log_ctx(request_id: str, msg: str, level: str = "info", **extra):
    """Standardized request context logger."""
    extra_parts = [f"{k}={v}" for k, v in extra.items()]
    extra_str = " | ".join(extra_parts)
    log_msg = f"{request_id} | {msg}"
    if extra_str:
        log_msg += f" | {extra_str}"
    getattr(logger, level)(log_msg)

_last_log_time = {}

def should_log_event(event_name: str, interval_seconds: int = 60) -> bool:
    """Rate-limit logging to prevent log flooding."""
    now = time.time()
    last = _last_log_time.get(event_name, 0)
    if now - last >= interval_seconds:
        _last_log_time[event_name] = now
        return True
    return False

def sanitize_stream_token(token: str) -> str:
    """Sanitize streaming tokens to prevent injection and control-character abuse.

    - Removes non-printable/control characters (except basic whitespace).
    - Trims surrounding whitespace and limits token length to 500 chars.
    """
    if not token:
        return ""
    # Remove ASCII control characters except for newline (\n), carriage return (\r) and tab (\t)
    try: # type: ignore
        cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', str(token))
    except Exception:
        cleaned = str(token)
    cleaned = cleaned.strip()
    if len(cleaned) > 500:
        return cleaned[:500]
    return cleaned

def validate_environment():
    """Production Hardening: Ensure critical environment variables are present."""
    # Check settings directly
    if not settings.ai_gateway_internal_secret.get_secret_value():
        logger.critical("CRITICAL | AI_GATEWAY_INTERNAL_SECRET is missing.")
        raise RuntimeError("AI_GATEWAY_INTERNAL_SECRET is required.")
    if not settings.jwt_secret.get_secret_value():
        logger.critical("CRITICAL | JWT_SECRET is missing.")
        raise RuntimeError("JWT_SECRET is required.")
    
    # Check for Tesseract dependency if OCR is enabled
    try:
        pytesseract.get_tesseract_version()
        logger.info("SYSTEM | BOOT | ✅ Tesseract OCR engine detected")
    except Exception:
        logger.warning("SYSTEM | BOOT | ⚠️ Tesseract OCR not found. Document indexing will not have OCR fallback.") # type: ignore

def sanitize_log(data: dict) -> dict:
    """Remove sensitive fields before logging."""
    sensitive = {"password", "secret", "token", "key", "authorization"}
    if not isinstance(data, dict): return {}
    return {k: "***" if any(s in k.lower() for s in sensitive) else v for k, v in data.items()}

# ============================================================
# ENUMS & DATA MODELS
# ============================================================
class RequestType(str, Enum):
    CHAT = "chat"
    GENERATE = "generate"
    EMBEDDINGS = "embeddings"

# ============================================================
# DATA MODELS
# ============================================================
class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    prompt: Optional[str] = Field(default=None, max_length=128000)
    messages: Optional[List[ChatMessage]] = Field(default=None)
    conversation_id: Annotated[Optional[str], Field(default=None, validation_alias=AliasChoices("conversation_id", "workstream_id"))] = None
    max_memory_messages: int = Field(default=20, ge=1, le=100)
    model: Optional[str] = None
    vertical: Optional[str] = None
    temperature: Optional[float] = Field(default=0.7, ge=0.0, le=1.5)
    max_tokens: Optional[int] = Field(default=1024, ge=1, le=32768)
    stream: bool = False

    model_config = ConfigDict(
        extra="ignore",
        populate_by_name=True
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_and_validate(cls, data: Any) -> Any:
        if isinstance(data, dict):
            p = data.get('prompt')
            m = data.get('messages')
            if not p and not m:
                raise ValueError("Either 'prompt' or 'messages' must be provided")
        return data

class StreamingChatRequest(ChatRequest):
    """Explicit model for streaming requests to improve OpenAPI documentation."""
    stream: bool = True

class EmbeddingRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=32000)
    model: Optional[str] = None

class ChatResponse(BaseModel):
    model: str
    response: str # Changed from 'answer' to 'response' to match implementation and Ollama nomenclature
    citations: List[dict] = Field(default_factory=list)
    risks: List[dict] = Field(default_factory=list)
    entities: List[dict] = Field(default_factory=list)
    clauses: List[dict] = Field(default_factory=list)
    done: bool
    conversation_id: str
    context: Optional[List[int]] = None
    total_duration: Optional[int] = None
    prompt_eval_count: Optional[int] = None
    eval_count: Optional[int] = None

# ============================================================
# PROFESSIONAL SERVICES DATA MODELS
# ============================================================
class RFPRequest(BaseModel):
    client_name: str
    rfp_title: str
    deadline: str
    key_strengths: str
    rfp_requirements: str
    past_wins_summary: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = Field(default=0.65, ge=0.0, le=1.5)
    max_tokens: Optional[int] = Field(default=4096, ge=1, le=8192) # Increased for full proposal

class RFPResponse(BaseModel):
    executive_summary: str
    understanding_client_needs: str
    approach_methodology: str
    team_credentials: str
    pricing_commercial_terms: str
    risk_mitigation_success_metrics: str
    next_steps: str
    model: str
    conversation_id: str
    total_duration: Optional[int] = None
    prompt_eval_count: Optional[int] = None
    eval_count: Optional[int] = None
# ============================================================
# STATE, CONVERSATION MEMORY & TRACKING
# ============================================================
active_requests_by_type = {
    RequestType.CHAT: 0,
    RequestType.GENERATE: 0,
    RequestType.EMBEDDINGS: 0
}
active_requests_by_model = defaultdict(int)
active_requests_lock = asyncio.Lock()

class RequestTracker:
    """Context manager for reliable active request tracking across models and types."""
    def __init__(self, request_type: RequestType, model: str):
        self.request_type = request_type
        self.model = model
        self.acquired = False
        self.start_time: Optional[float] = None

    async def __aenter__(self):
        async with active_requests_lock:
            active_requests_by_type[self.request_type] += 1
            active_requests_by_model[self.model] += 1
            active_requests_gauge.labels(
                request_type=self.request_type.value,
                model=self.model
            ).inc()
            model_active_gauge.labels(model=self.model).inc()
            self.acquired = True
            self.start_time = time.perf_counter()
        logger.debug(f"SYSTEM | TRACKER | ACQUIRED | {self.request_type.value} | {self.model}")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.acquired:
            async with active_requests_lock:
                active_requests_by_type[self.request_type] = max(0, active_requests_by_type[self.request_type] - 1)
                active_requests_by_model[self.model] = max(0, active_requests_by_model[self.model] - 1)
            active_requests_gauge.labels(
                request_type=self.request_type.value,
                model=self.model
            ).dec()
            model_active_gauge.labels(model=self.model).dec()
            if self.start_time is not None and exc_type is None:
                inference_duration_histogram.labels(
                    model=self.model,
                    request_type=self.request_type.value
                ).observe(time.perf_counter() - self.start_time)
            self.acquired = False
            logger.debug(f"SYSTEM | TRACKER | RELEASED | {self.request_type.value} | {self.model}")
# ============================================================
# REDIS CONFIGURATION - FINAL PRODUCTION VERSION
# ============================================================

conversation_store: dict = {}
conversation_lock = asyncio.Lock()
redis_client: Optional[aioredis.Redis] = None

# Build immediately
build_redis_url()

class RedisCircuitBreaker:
    """Circuit breaker for Redis to prevent cascading failures under outage."""

    def __init__(self, threshold: int = 3, reset_timeout: int = 60):
        self.failures = 0
        self.last_failure = 0.0
        self.threshold = threshold
        self.reset_timeout = reset_timeout
        self.lock = asyncio.Lock()

    async def execute(self, coro, fallback=None):
        async with self.lock:
            if self.failures >= self.threshold:
                elapsed = time.time() - self.last_failure
                if elapsed < self.reset_timeout:
                    redis_cb_state_gauge.set(1) # Open
                    return fallback
                self.failures = self.threshold - 1

        try:
            result = await asyncio.wait_for(coro, timeout=2.0)
            async with self.lock:
                redis_cb_state_gauge.set(0) # Closed
                if self.failures > 0:
                    logger.info("REDIS | Circuit breaker recovered")
                self.failures = 0
            return result
        except (asyncio.TimeoutError, Exception) as e:
            async with self.lock:
                self.failures += 1
                self.last_failure = time.time()
                redis_cb_state_gauge.set(1) # Open
                logger.warning(f"REDIS | Circuit failure {self.failures}/{self.threshold}: {type(e).__name__}")
            return fallback

redis_circuit_breaker = RedisCircuitBreaker(threshold=3, reset_timeout=60)

async def safe_redis_op(coro, default=None):
    """Ultra-safe Redis wrapper with circuit breaker."""
    if not redis_client:
        return default
    return await redis_circuit_breaker.execute(coro, fallback=default)

async def init_redis():
    """Robust initialization with retries"""
    global redis_client
    current_url = build_redis_url()
    password_for_log = os.getenv("REDIS_PASSWORD") or os.getenv("REDIS_PASS", "")

    for attempt in range(1, 6):
        try:
            redacted_url = current_url
            if password_for_log:
                # Mask the encoded password correctly in startup logs
                encoded_pass = quote(str(password_for_log), safe='')
                redacted_url = current_url.replace(encoded_pass, "[REDACTED]")
            
            logger.info(f"REDIS | Connection attempt {attempt}/5 | URL: {redacted_url} | Source: {'REDIS_PASSWORD' if os.getenv('REDIS_PASSWORD') else 'REDIS_PASS' if os.getenv('REDIS_PASS') else 'None'}")
            redis_client = aioredis.from_url(
                current_url,
                decode_responses=True,
                socket_connect_timeout=int(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "15")),
                socket_timeout=int(os.getenv("REDIS_SOCKET_TIMEOUT", "12")),
                socket_keepalive=True,
                socket_keepalive_options={
                    socket.TCP_KEEPIDLE: 60,
                    socket.TCP_KEEPINTVL: 10,
                    socket.TCP_KEEPCNT: 3,
                },
                retry_on_timeout=True,
                max_connections=REDIS_MAX_CONNECTIONS,
                health_check_interval=30
            )
            # aioredis.ping() can return True (boolean) or "PONG" (string)
            pong = await asyncio.wait_for(redis_client.ping(), timeout=5.0)
            # Check for both boolean True and string "PONG"
            if pong is not True and str(pong).upper() != "PONG":
                raise ValueError(f"Unexpected ping response: {pong}")

            logger.info(f"REDIS | ✅ Connected successfully (Attempt {attempt})")
            readiness_state["redis"] = True
            try:
                # Log pool statistics for production monitoring
                pool = redis_client.connection_pool
                avail = len(pool._available_connections)
                in_use = len(pool._in_use_connections)
                logger.info(f"REDIS | Pool metrics | max: {REDIS_MAX_CONNECTIONS} | avail: {avail} | in_use: {in_use}")
            except Exception:
                pass
            # UPDATE Distributed Metrics client
            if 'distributed_metrics' in globals():
                distributed_metrics.redis = redis_client
            return
        except Exception as e:
            logger.warning(f"REDIS | Connection attempt {attempt}/5 failed: {type(e).__name__} - {e}")
            if redis_client:
                try:
                    await redis_client.aclose()
                except:
                    pass
            redis_client = None
            await asyncio.sleep(min(10, 1.5 ** attempt))

    logger.error("REDIS | ❌ Failed to connect after retries. Operating in MEMORY-ONLY mode.")
    redis_client = None

# ============================================================
# USAGE TRACKING
# ============================================================
usage_store = defaultdict(int)
usage_lock = asyncio.Lock()

def usage_bucket_key(user_id: str) -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    return f"{user_id}:{today}"

async def get_user_usage(user_id: str) -> int:
    """Get current token usage for the day with Redis-first + memory fallback."""
    if not user_id:
        return 0

    key = usage_bucket_key(user_id) # Ensure this key is consistent with incr_user_usage

    # Try Redis first via safe wrapper
    val = None
    if redis_client:
        val = await safe_redis_op(redis_client.get(f"usage:{key}"))
    if val is not None: # Check if val was successfully retrieved from Redis
        logger.debug(f"REDIS | Usage cache HIT for {key}: {val}")
        return int(val) if val else 0

    logger.debug(f"REDIS | Usage cache MISS for {key}, using memory")

    # Memory fallback
    async with usage_lock:
        return usage_store[key]

async def incr_user_usage(user_id: str, tokens: int):
    if not user_id or tokens <= 0:
        return

    key = usage_bucket_key(user_id)
    lock_key = f"usage_lock:{key}"

    async with RedisLock(redis_client, lock_key, ttl=8):
        if redis_client:
            async def _redis_incr():
                await redis_client.incrby(f"usage:{key}", tokens)
                await redis_client.expire(f"usage:{key}", 172800)
            await safe_redis_op(_redis_incr())

        async with usage_lock:
            usage_store[key] += tokens

async def record_actual_usage(
    identity_key: str,
    model: str,
    prompt_eval_count: Optional[int] = None,
    eval_count: Optional[int] = None,
    request_type: str = "chat",
    request: Optional[Request] = None,
) -> int:
    if prompt_eval_count is None or eval_count is None: # type: ignore
        tokens_used = 250 if "embed" in request_type else 480
    else:
        tokens_used = max(0, int(prompt_eval_count)) + max(0, int(eval_count))

    await incr_user_usage(identity_key, tokens_used)

    if request is not None:
        request.state.tokens_used_this_request = tokens_used
        request.state.total_usage_after_request = await get_user_usage(identity_key)
        if getattr(request.state, "kyc_grace_mode", False): # type: ignore
            uid = None
            state_user = getattr(request.state, "user", None)
            if isinstance(state_user, dict) and state_user.get("user_id"):
                uid = str(state_user["user_id"])
            elif isinstance(identity_key, str) and ":user:" in identity_key:
                uid = identity_key.rsplit(":user:", 1)[-1]
            record_grace_usage_metric(tokens_used, user_id=uid)

    asyncio.create_task(
        log_ai_usage(
            user_id=identity_key,
            model=model,
            tokens_used=tokens_used,
            request_type=request_type,
            request_id=getattr(request.state, "request_id", "unknown") if request else "unknown"
        )
    )

    return tokens_used


class LRUEmbeddingCache:
    """Thread-safe LRU cache for embeddings with O(1) get/set operations."""
    def __init__(self, maxsize: int = 1000):
        self.cache: OrderedDict = OrderedDict()
        self.maxsize = maxsize
        self.lock = threading.Lock()
    
    def get(self, key: str) -> Optional[List[float]]:
        """Get embedding from cache, moves to end (most recent)."""
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
        return None
    
    def set(self, key: str, value: List[float]) -> None:
        """Set embedding in cache, evicting oldest if needed."""
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            else:
                if len(self.cache) >= self.maxsize:
                    # Remove oldest (first) item
                    self.cache.popitem(last=False)
                self.cache[key] = value


embedding_cache = LRUEmbeddingCache(maxsize=settings.embedding_cache_max_size)
embedding_client: Optional[httpx.AsyncClient] = None

async def create_embedding(text: str) -> List[float]:
    """Generate embedding for a piece of text using Ollama."""
    if not text:
        return [] # type: ignore
        
    cache_key = hashlib.md5(text.encode()).hexdigest()
    cached = embedding_cache.get(cache_key)
    if cached:
        return cached

    try:
        if not embedding_client:
            raise RuntimeError("Embedding client not initialized") # type: ignore

        resp = await embedding_client.post(
            "/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text[:8000]}, # Truncate to protect context floor
            timeout=httpx.Timeout(45.0)
        )
        resp.raise_for_status()
        embedding = resp.json().get("embedding", [])
        
        if embedding: # type: ignore
            embedding_cache.set(cache_key, embedding)
        return embedding
    except Exception as e:
        logger.warning(f"EMBEDDING | Fallback to sentence-transformers: {e}")
        if SentenceTransformer: # type: ignore
            # Local CPU fallback for production resilience
            # Ensure fallback model matches the 1024 dimensions expected by Qdrant
            fallback_model = "mixedbread-ai/mxbai-embed-large-v1" if EMBEDDING_MODEL == "mxbai-embed-large" else "all-MiniLM-L6-v2"
            model = SentenceTransformer(fallback_model)
            return model.encode(text).tolist()
        return []

async def get_conversation_id(identity_key: str, provided_id: Optional[str], org_id: int = 0) -> str:
    """Get or create conversation ID (memory-first) with tenant context."""
    cid = provided_id or f"c_{uuid.uuid4().hex[:12]}"
    key = (identity_key, cid)
 # type: ignore
    # Check memory first without lock for read-heavy access
    if key in conversation_store:
        conversation_store[key]["last_seen"] = time.time() # Update last seen
        return cid
    async with conversation_lock: # Acquire lock only if not found
        if key not in conversation_store: # Double check after acquiring lock
            if len(conversation_store) >= MAX_CONVERSATIONS: # Prune if necessary
                oldest_key = min(conversation_store.keys(), key=lambda k: conversation_store[k]["last_seen"]) # Oldest by last_seen
                del conversation_store[oldest_key]
            conversation_store[key] = {
                "messages": deque(maxlen=MAX_MESSAGES_PER_CONVO),
                "last_seen": time.time(),
                "org_id": org_id
            }
        conversation_store[key]["last_seen"] = time.time()
        return cid
    
async def update_conversation_history(
    identity_key: str,
    conversation_id: str,
    user_content: str,
    assistant_content: Optional[str] = None,
    org_id: int = 0
):
    """Write-through: Update memory and Redis history with distributed locking."""
    ts = int(time.time())
    key = (identity_key, conversation_id)
    lock_key = f"conv_lock:{identity_key}:{conversation_id}"

    async with RedisLock(redis_client, lock_key, ttl=15):
        async with conversation_lock:
            if key not in conversation_store:
                conversation_store[key] = {
                    "messages": deque(maxlen=MAX_MESSAGES_PER_CONVO),
                    "last_seen": ts, # type: ignore
                    "org_id": org_id
                }
            convo = conversation_store[key]
            messages: deque = convo["messages"]

            # Deduplication
            if not messages or messages[-1].get("content") != user_content:
                messages.append({"role": "user", "content": user_content, "ts": ts})
            if assistant_content:
                messages.append({"role": "assistant", "content": assistant_content, "ts": ts})

            convo["last_seen"] = ts
            if org_id:
                convo["org_id"] = org_id

        # Best-effort Redis
        if redis_client:
            try:
                rkey = f"conv:{identity_key}:{conversation_id}"
                mkey = f"{rkey}:messages"
                pipe = redis_client.pipeline(transaction=False)
                pipe.hset(rkey, mapping={"last_seen": ts, "identity": identity_key, "org_id": org_id}) # type: ignore
                pipe.expire(rkey, REDIS_TTL)
                pipe.rpush(mkey, json.dumps({"role": "user", "content": user_content, "ts": ts}))
                if assistant_content:
                    pipe.rpush(mkey, json.dumps({"role": "assistant", "content": assistant_content, "ts": ts}))
                pipe.ltrim(mkey, -MAX_MESSAGES_PER_CONVO, -1)
                pipe.expire(mkey, REDIS_TTL)
                await asyncio.shield(pipe.execute())
            except Exception as e:
                logger.debug(f"REDIS | Conv write failed (memory ok): {e}")

async def load_conversation(identity_key: str, conversation_id: str, org_id: int = 0) -> Optional[dict]:
    """Redis-first read with seamless memory fallback and tenant validation."""
    if redis_client:
        try:
            rkey = f"conv:{identity_key}:{conversation_id}"
            mkey = f"{rkey}:messages"

            meta, raw_messages = await asyncio.gather(
                redis_client.hgetall(rkey),
                redis_client.lrange(mkey, 0, -1),
                return_exceptions=True
            )
 # type: ignore
            if isinstance(meta, dict) and meta and isinstance(raw_messages, list) and raw_messages:
                # Validate tenant if org_id is provided (Production Fix)
                if org_id and int(meta.get("org_id", 0)) != org_id:
                    logger.warning(f"TENANT | Access denied to {conversation_id} | Identity: {identity_key} | Required Org: {org_id} | Found: {meta.get('org_id')}")
                    return None

                return {
                    "messages": [json.loads(m) for m in raw_messages],
                    "last_seen": float(meta.get("last_seen", 0)),
                    "org_id": int(meta.get("org_id", 0))
                }
        except Exception as e:
            logger.debug(f"REDIS | Read failed, falling back to memory: {e}")

    # Memory fallback
    async with conversation_lock:
        data = conversation_store.get((identity_key, conversation_id))
        if data and org_id and data.get("org_id", 0) != org_id: # Tenant validation for memory fallback
            return None
        return data

async def list_conversations_redis(identity_key: str, org_id: int = 0) -> List[dict]:
    """List conversation summaries from Redis when shared state is available."""
    if not redis_client: return []
    results: List[dict] = [] # type: ignore
    seen_conversations: Set[str] = set()
    try:
        cursor = 0
        pattern = f"conv:{identity_key}:*"
        while True:
            cursor, keys = await redis_client.scan(cursor, match=pattern, count=100)
            for rkey in keys:
                if rkey.endswith(":messages"): continue
                cid = rkey.split(":")[-1]
                if not cid or cid in seen_conversations: continue
                seen_conversations.add(cid)

                mkey = f"{rkey}:messages" # type: ignore
                meta, msg_count, first_msg_raw = await asyncio.gather(
                    redis_client.hgetall(rkey),
                    redis_client.llen(mkey),
                    redis_client.lindex(mkey, 0),
                    return_exceptions=True,
                )
                if not isinstance(meta, dict) or not meta: continue
                title = "New conversation"
                if isinstance(first_msg_raw, str) and first_msg_raw:
                    try:
                        first_msg = json.loads(first_msg_raw)
                        if first_msg.get("role") == "user":
                            content = str(first_msg.get("content", "")).strip()
                            if content:
                                title = (content[:55] + "...") if len(content) > 55 else content
                    except Exception:
                        pass

                last_seen = float(meta.get("last_seen", 0) or 0)
                item_org_id = int(meta.get("org_id", 0))
                
                # Skip if org doesn't match
                if org_id and item_org_id != org_id:
                    continue

                results.append({
                    "conversation_id": cid,
                    "title": title,
                    "last_seen": datetime.fromtimestamp(last_seen, timezone.utc).isoformat(),
                    "message_count": int(msg_count) if not isinstance(msg_count, Exception) else 0,
                    "org_id": item_org_id
                })

            if cursor == 0:
                break
    except Exception as e:
        logger.debug(f"REDIS | List conversations failed for {identity_key}: {e}")
        return []
    results.sort(key=lambda x: x["last_seen"], reverse=True)
    return results

async def cleanup_conversations():
    """Background task to clean up old conversations in memory."""
    while True:
        try:
            await asyncio.sleep(300) # type: ignore
            now = time.time()
            async with conversation_lock:
                to_delete = [key for key, data in conversation_store.items() if now - data["last_seen"] > CONVERSATION_TTL_SECONDS]
                for key in to_delete:
                    del conversation_store[key]
                if to_delete:
                    logger.info(f"SYSTEM | MEMORY | Cleaned up {len(to_delete)} expired conversations")
        except Exception as e:
            logger.error(f"SYSTEM | MEMORY | Cleanup task error: {str(e)}")

async def build_messages_with_memory(identity_key: str, conversation_id: str, incoming: List[dict], max_messages: int, org_id: int = 0) -> List[dict]:
    """Load from Redis (if available) or memory, then combine with incoming."""
    loaded = await load_conversation(identity_key, conversation_id, org_id)
    memory_msgs = loaded["messages"] if loaded else [] # type: ignore

    if isinstance(memory_msgs, deque): # Convert deque to list for concatenation
        memory_msgs = list(memory_msgs)

    return memory_msgs[-max_messages:] + incoming

# ============================================================
# REDIS-AWARE DELETE HELPER
# ============================================================
async def delete_conversation(identity_key: str, conversation_id: str, org_id: int = 0):
    key = (identity_key, conversation_id)
    async with conversation_lock:
        data = conversation_store.get(key)
        if data and org_id and data.get("org_id", 0) != org_id: # Tenant validation for delete
            logger.warning(f"TENANT | Blocked delete for {conversation_id} | Identity: {identity_key} | Required Org: {org_id}")
            return
        conversation_store.pop(key, None)

    async def _redis_del():
        rkey = f"conv:{identity_key}:{conversation_id}"
        # Skip if Redis record exists and org doesn't match
        if org_id: # Tenant validation for Redis delete
            meta = await redis_client.hgetall(rkey)
            if meta and int(meta.get("org_id", 0)) != org_id:
                return # type: ignore
        mkey = f"{rkey}:messages"
        await asyncio.gather(redis_client.delete(rkey), redis_client.delete(mkey))
    if redis_client:
        await safe_redis_op(_redis_del())

async def redis_maintenance_loop():
    """Background task to ensure Redis connectivity and perform health checks."""
    backoff = 1
    max_backoff = 300 # type: ignore
    while True:
        try:
            await asyncio.sleep(backoff)
            if redis_client:
                try:
                    await asyncio.wait_for(redis_client.ping(), timeout=2.0)
                    backoff = 1
                except Exception:
                    logger.warning("REDIS | Connection lost, attempting reconnect...")
                    await init_redis()
                    backoff = 1 if redis_client else min(backoff * 2, max_backoff)
            else:
                # Attempt to initialize if it was previously disabled/failed
                if REDIS_URL and REDIS_URL != "redis://localhost:6379/0":
                    await init_redis()
                    backoff = 1 if redis_client else min(backoff * 2, max_backoff)
        except Exception as e:
            logger.debug(f"SYSTEM | Redis maintenance error: {e}")
            backoff = min(backoff * 2, max_backoff)

# ============================================================
# REDIS DISTRIBUTED LOCKING (Production Ready)
# ============================================================

@asynccontextmanager
async def distributed_lock(lock_key: str, ttl: int = 45):
    lock = RedisLock(redis_client, lock_key, ttl)
    async with lock:
        yield

# ============================================================
# BACKGROUND TASKS (Warmup & Cleanup)
# ============================================================

readiness_state = {"llm": False, "embedding": False, "redis": False, "qdrant": False}
provider_status = {
    "ollama": {
        "reachable": False,
        "last_checked": None,
        "models": [],
        "error": None,
    }
}

# Helper function for warmup calls, moved outside the loop for efficiency
async def _warmup_call_helper(model, path, payload):
    if model not in model_semaphores or ollama_client is None:
        raise RuntimeError(f"Warmup unavailable for model={model}")
    async with model_semaphores[model]:
        return await ollama_client.post(path, json=payload)


async def _warmup_impl():
    # Skip warmup in worker processes to prevent CPU thrashing
    if os.getenv("SKIP_WARMUP", "false").lower() == "true":
        logger.info("SYSTEM | INIT | Warmup skipped via environment configuration")
        readiness_state["llm"] = readiness_state["embedding"] = True
        return

    # Detect if this is a Gunicorn worker process (not master)
    if os.getenv("GUNICORN_WORKER", "false").lower() == "true":
        logger.info("SYSTEM | INIT | Skipping warmup in Gunicorn worker - will warmup on first request")
        # Don't mark as ready immediately - first request will trigger warmup
        return

    lock = RedisLock(redis_client, "warmup:models:global", ttl=45)
    if not await lock.acquire(blocking=True, timeout=20.0):
        logger.warning("SYSTEM | INIT | Warmup lock not acquired; another worker may be warming up")
        return

    try:
        if readiness_state.get("llm") and readiness_state.get("embedding"):
            logger.info("SYSTEM | INIT | Warmup already completed by another worker")
            return

        # Add a small delay to allow other services to settle
        await asyncio.sleep(2)
        
        max_failures = 5  # Reduced from 10
        failures = 0
        
        # Single warmup attempt - if it fails, mark ready anyway (degraded mode)
        try:
            if ollama_client is None:
                logger.warning("SYSTEM | INIT | Ollama client not ready, skipping warmup")
                readiness_state["llm"] = readiness_state["embedding"] = True
                return
                
            # Quick health check only - no heavy inference
            if not readiness_state["llm"]:
                try:
                    # Just check if Ollama is reachable, don't run inference
                    resp = await ollama_client.get("/api/tags", timeout=httpx.Timeout(10.0))
                    readiness_state["llm"] = resp.status_code == 200
                    if readiness_state["llm"]:
                        logger.info("SYSTEM | INIT | LLM provider reachable")
                except Exception as e:
                    logger.warning(f"SYSTEM | INIT | LLM health check failed: {e}")
                    readiness_state["llm"] = False

            if not readiness_state["embedding"]:
                try:
                    resp = await ollama_client.get("/api/tags", timeout=httpx.Timeout(10.0))
                    readiness_state["embedding"] = resp.status_code == 200
                    if readiness_state["embedding"]:
                        logger.info("SYSTEM | INIT | Embedding provider reachable")
                except Exception as e:
                    logger.warning(f"SYSTEM | INIT | Embedding health check failed: {e}")
                    readiness_state["embedding"] = False

        except Exception as e:
            logger.warning(f"SYSTEM | INIT | Warmup failed: {e}")
            failures += 1

        # Mark ready anyway - let first request handle actual warmup
        if not readiness_state.get("llm"):
            readiness_state["llm"] = True
            logger.info("SYSTEM | INIT | LLM marked ready (degraded mode - warmup on first request)")
        if not readiness_state.get("embedding"):
            readiness_state["embedding"] = True
            logger.info("SYSTEM | INIT | Embedding marked ready (degraded mode - warmup on first request)")

        logger.info(f"SYSTEM | INIT | Warmup complete - readiness: {readiness_state}")
    finally:
        await lock.release()

async def warmup_loop():
    """Warm up models under a global distributed lock (multi-worker safe)."""
    try:
        await asyncio.wait_for(_warmup_impl(), timeout=60.0)
    except asyncio.TimeoutError:
        logger.warning("SYSTEM | INIT | Warmup timed out, continuing in degraded mode")

async def refresh_ollama_provider_status() -> dict:
    """Probe Ollama without disabling the shared client if startup is degraded."""
    status = provider_status["ollama"]
    status["last_checked"] = datetime.now(timezone.utc).isoformat()

    if ollama_client is None:
        status["reachable"] = False
        status["models"] = []
        status["error"] = "client_not_initialized" # type: ignore
        return status

    try:
        response = await retry_ollama_request("GET", "/api/tags", LLM_MODEL, max_retries=1, bypass_cb=True)
        models = response.json().get("models", [])
        status["reachable"] = True
        status["models"] = [m.get("name") for m in models if isinstance(m, dict) and m.get("name")]
        status["error"] = None # type: ignore
        logger.info(f"SYSTEM | OLLAMA | Provider reachable with {len(status['models'])} model(s)")
    except Exception as e:
        status["reachable"] = False
        status["models"] = []
        status["error"] = str(e)
        logger.error(f"SYSTEM | OLLAMA | Provider probe failed: {e}")

    return status

async def cleanup_grace_metrics():
    """Prune stale in-process grace usage counters to prevent unbounded growth."""
    while True:
        await asyncio.sleep(86400) # type: ignore
        try:
            today = datetime.now(timezone.utc).date().isoformat()
            stale = [d for d in grace_usage_by_date if d != today]
            for date in stale:
                del grace_usage_by_date[date]
            if stale:
                logger.info(f"SYSTEM | GRACE | Cleaned up {len(stale)} stale metric dates")
        except Exception as e:
            logger.error(f"SYSTEM | GRACE | Cleanup error: {e}")

async def cleanup_rate_limits():
    """Background task to prune old rate limit windows."""
    while True:
        await asyncio.sleep(300)  # Run every 5 minutes # type: ignore
        try:
            now = time.time()
            async with rate_limit_lock:
                # Memory growth protection: Hard cap on entries
                if len(rate_limit_store) > 50000:
                    # Sort by last access (oldest first)
                    sorted_keys = sorted(rate_limit_store.keys(), key=lambda k: rate_limit_store[k][-1] if rate_limit_store[k] else 0)
                    for k in sorted_keys[:10000]:
                        del rate_limit_store[k]

                keys_to_del = [
                    k for k, v in list(rate_limit_store.items())  # safe copy for iteration
                    if not v or (now - v[-1] > 3600)  # last access too old
                ]
                for k in keys_to_del:
                    del rate_limit_store[k]
                if keys_to_del:
                    logger.info(f"SYSTEM | RATE_LIMIT | Cleaned up {len(keys_to_del)} old rate windows")
                    
            # Periodic Auth Cache TTL cleanup
            async with auth_cache_lock:
                expired = [k for k, (_, exp) in auth_cache.items() if time.time() > exp]
                for k in expired: auth_cache.pop(k, None)
        except Exception as e:
            logger.error(f"SYSTEM | RATE_LIMIT | Cleanup error: {e}")
# ============================================================
# LIFESPAN HANDLER (Startup/Shutdown)
# ============================================================

ollama_client: Optional[httpx.AsyncClient] = None
auth_client: Optional[httpx.AsyncClient] = None
auth_internal_client: Optional[httpx.AsyncClient] = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Unified lifespan handler for application startup and shutdown."""
    global ollama_client, auth_client, auth_internal_client, qdrant_client, embedding_client

    # 1. Initialize Ollama Client
    # Production Hardening: Startup Validation (uses settings)
    validate_environment()

    ollama_client = httpx.AsyncClient(
        base_url=OLLAMA_HOST,
        timeout=REQUEST_TIMEOUT,
        limits=httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=30)
    )

    # Initialize specialized Embedding Client
    embedding_client = httpx.AsyncClient(
        base_url=OLLAMA_HOST,
        timeout=httpx.Timeout(45.0, connect=10.0),
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=30)
    )

    # 2. Initialize Auth Client (Isolated pool)
    auth_client = httpx.AsyncClient(
        base_url=settings.auth_api_base,
        timeout=httpx.Timeout(10.0),
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20, keepalive_expiry=30)
    )
    auth_internal_client = httpx.AsyncClient(
        base_url=settings.auth_internal_base,
        timeout=httpx.Timeout(10.0),
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10, keepalive_expiry=30)
    )

    logger.info("=" * 60)
    logger.info(f"ARYBIT CLOUD CORE AI NODE v2.3.0 starting | Auth Mode: {settings.auth_mode.upper()}")
    logger.info(f"Ollama Endpoint : {settings.ollama_host}")
    logger.info(f"Identity Service: {settings.auth_api_base}")
    logger.info(f"Internal Service: {settings.auth_internal_base}")
    logger.info(f"SYSTEM | Generous KYC Grace Mode Active: {settings.kyc_grace_max_tokens} tokens / {settings.kyc_grace_max_prompt_chars} chars")
    logger.info("=" * 60)


    if settings.auth_mode == "remote" and not INTERNAL_SERVICE_SECRET: # Critical check
        logger.critical("CRITICAL | Internal service secret missing in REMOTE auth mode")
        raise RuntimeError("AI_GATEWAY_INTERNAL_SECRET is required for remote auth mode")

    # Strengthen security: Ensure internal secret is sufficiently complex (uses settings)
    if not INTERNAL_SERVICE_SECRET or len(INTERNAL_SERVICE_SECRET) < 48:
        logger.critical("CRITICAL | AI_GATEWAY_INTERNAL_SECRET is missing or too weak (must be >= 48 chars)")
        raise RuntimeError("AI_GATEWAY_INTERNAL_SECRET is required and must be at least 48 characters long.")

    log_gateway_secret_event(
        logger,
        event="startup_configured",
        configured_secret=INTERNAL_SERVICE_SECRET,
        extra={
            "env_key": _INTERNAL_SECRET_ENV_KEY,
            "fingerprint": gateway_secret_fingerprint(INTERNAL_SERVICE_SECRET),
        },
    )

    if not settings.jwt_secret.get_secret_value(): # Log if JWT_SECRET is not explicitly set
        logger.info(f"SYSTEM | Using fallback internal secret for delegated worker JWT validation (Len: {len(JWT_SECRET)})")
    else:
        logger.info(f"SYSTEM | JWT_SECRET loaded (Len: {len(JWT_SECRET)})")

    logger.info(f"SYSTEM | Allowed Models: {list(settings.allowed_models)}")
    logger.info(f"SYSTEM | Concurrency Matrix: {MODEL_CONCURRENCY}")
    logger.info(f"SYSTEM | Request Queue Limit: {settings.global_max_concurrent_requests}")
    logger.info(f"SYSTEM | Internal Service Name: {settings.internal_service_name}")
    logger.info(f"SYSTEM | Trusted Background Services: {sorted(TRUSTED_BACKGROUND_SERVICES)}")
    
    register_model_limit_metrics() # type: ignore
    set_global_admission_metrics()

    if settings.auth_mode == "local" and not JWT_SECRET:
        logger.critical("CRITICAL | JWT_SECRET missing in LOCAL auth mode")
        raise RuntimeError("JWT_SECRET is required for local auth mode")

    # Hardened check for default development keys
    if any(k in settings.api_keys for k in {"dev-key", "arybit-dev", "test-key", "your-api-key"}): # Added common default
        logger.warning("SECURITY | ⚠️ Default or development API_KEYS detected. Please configure unique, high-entropy keys for production.")

    # Initialize Qdrant Client
    logger.info(f"QDRANT | Initializing connection to {settings.qdrant_host}")
    qdrant_client = AsyncQdrantClient(url=settings.qdrant_host)
    
    # Determine vector dimension based on model
    vector_size = 1024 # Default for mxbai-embed-large
    if "nomic" in EMBEDDING_MODEL.lower():
        vector_size = 768

    # CORE PLATFORM BOOT: Initialize Standard Collections
    # CORE PLATFORM BOOT: Initialize Standard Collections (FIXED)
    platform_collections = ["vectors_core", "vectors_legal", "vectors_professional_services", "vectors_fintech", "vectors_aarab"]
    for coll in platform_collections:
        collection_ready = False
        for attempt in range(3):
            try:
                exists = await qdrant_client.collection_exists(coll)
                if not exists:
                    logger.info(f"QDRANT | Creating collection '{coll}' (dim={vector_size})") # type: ignore
                    await qdrant_client.create_collection(
                        collection_name=coll,
                        vectors_config=models.VectorParams(size=vector_size, distance=models.Distance.COSINE),
                        timeout=30
                    )
                collection_ready = True
                break  # Success - exit retry loop
            except Exception as e:
                err_lower = str(e).lower()
                if "already exists" in err_lower or "409" in err_lower:
                    logger.info(f"QDRANT | Collection '{coll}' already exists (worker race condition)")
                    collection_ready = True
                    break  # Collection already exists - success case
                if attempt == 2:
                    logger.error(f"QDRANT | Failed to ensure collection {coll} after 3 attempts: {e}")
                    # Don't raise - allow degraded operation
                else:
                    logger.warning(f"QDRANT | Collection {coll} attempt {attempt+1} failed: {e}")
                    await asyncio.sleep(2 ** attempt)
    
        if not collection_ready:
            logger.warning(f"QDRANT | Collection {coll} may not be ready - continuing in degraded mode")
    
    readiness_state["qdrant"] = True
    
    # Initial collection size metric (moved outside the loop, after all collections processed)
    try:
        for coll in PLATFORM_COLLECTIONS:
            if await qdrant_client.collection_exists(coll):
                info = await qdrant_client.get_collection(coll) # type: ignore
                qdrant_collection_size.labels(collection=coll).set(info.points_count)
    except Exception as e:
        logger.debug(f"QDRANT | Failed to fetch collection metrics: {e}")

    await init_redis()
    if redis_client:
        try:
            await redis_client.ping()
            logger.info("REDIS | Connection verified on startup")
        except Exception as e:
            logger.warning(f"REDIS | Startup ping failed: {e}")

    await refresh_ollama_provider_status()

    logger.info(f"SYSTEM | Redis Mode: {'ENABLED' if redis_client else 'MEMORY-ONLY'}") # type: ignore
    asyncio.create_task(warmup_loop())
    asyncio.create_task(cleanup_conversations())
    # UPDATE Distributed Metrics client
    if redis_client and 'distributed_metrics' in globals():
        distributed_metrics.redis = redis_client

    asyncio.create_task(cleanup_rate_limits())
    asyncio.create_task(cleanup_grace_metrics())
    asyncio.create_task(redis_maintenance_loop())
    asyncio.create_task(cleanup_abandoned_uploads())

    app.state.start_time = time.time()

    yield

    # Shutdown sequence
    logger.info("🛑 ARYBIT CLOUD CORE AI NODE - SHUTTING DOWN")
    # Force Celery shutdown signal
    try:
        celery_app.control.shutdown()
    except Exception as e: # type: ignore
        logger.warning(f"SYSTEM | SHUTDOWN | Celery control shutdown failed: {e}")

    shutdown_tasks = [ollama_client.aclose(), embedding_client.aclose(), auth_client.aclose(), auth_internal_client.aclose()]
    if redis_client:
        shutdown_tasks.append(redis_client.aclose())
    if qdrant_client:
        shutdown_tasks.append(qdrant_client.close())
    await asyncio.gather(*shutdown_tasks, return_exceptions=True)

app = FastAPI(
    title="Arybit Cloud Core AI Node",
    description="Production-grade AI inference gateway with Ollama integration",
    version="2.3.0",
    lifespan=lifespan
)

# Enable Prometheus Metrics
Instrumentator().instrument(app).expose(app)

if OTEL_ENABLED:
    FastAPIInstrumentor.instrument_app(app)
    # Instrument the global httpx library
    HTTPXClientInstrumentor().instrument()

# Mount Static Files (CSS, JS, Images)
frontend_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
if os.path.exists(frontend_path):
    app.mount("/css", StaticFiles(directory=os.path.join(frontend_path, "css")), name="css")
    app.mount("/js", StaticFiles(directory=os.path.join(frontend_path, "js")), name="js")
    app.mount("/img", StaticFiles(directory=os.path.join(frontend_path, "img")), name="img")

# ============================================================
# MIDDLEWARE CLASSES
# ============================================================
class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method == "POST":
            # Check Content-Length header first (fastest)
            cl = request.headers.get("content-length") # type: ignore
            try:
                content_length = int(cl) if cl else 0
            except (ValueError, TypeError):
                content_length = 0
            if content_length > MAX_REQUEST_SIZE:
                return JSONResponse(status_code=413, content={"error": "Payload too large"})
            
            # CRITICAL FIX: Do NOT consume multipart or urlencoded bodies in middleware
            # FastAPI's Form/File dependencies need to read the stream directly.
            # Rely on Content-Length for size check for these types. # type: ignore
            content_type = request.headers.get("content-type", "").lower()
            if "multipart/form-data" in content_type or "application/x-www-form-urlencoded" in content_type:
                return await call_next(request) # Skip body consumption for multipart
            
            try:
                # Safeguard against body flooding if header was spoofed or missing
                # Use a timeout for body reading to prevent hanging on slow clients
                body = await asyncio.wait_for(request.body(), timeout=5.0) # type: ignore
                if len(body) > MAX_REQUEST_SIZE:
                    return JSONResponse(status_code=413, content={"error": "Payload too large"})
                # Re-set body for downstream processing
                request._body = body
            except asyncio.TimeoutError:
                logger.warning(f"{getattr(request.state, 'request_id', 'unknown')} | REQUEST_SIZE_LIMIT | Request body timeout")
                return JSONResponse(status_code=408, content={"error": "Request body timeout"})
            except Exception as e:  # Catch any other unexpected errors during body read
                # Log the error and re-raise it, as this indicates an unexpected issue
                if not getattr(request.state, "is_noisy", False):
                    logger.error(f"SYSTEM | Body read failure: {e}")
                raise  # Re-raise the exception to prevent silent failures
        return await call_next(request)

class SlowQueryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        duration = time.time() - start
        
        if duration > 5.0:
            request_id = getattr(request.state, "request_id", "unknown")
            logger.warning(
                f"{request_id} | Slow request: {request.method} {request.url.path} took {duration:.2f}s"
            )
        return response

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        token = bind_request_id(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally: # type: ignore
            reset_request_id(token)


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Structured access line per request (complements NGINX access.log)."""

    QUIET_PATHS = frozenset({"/health", "/healthz", "/metrics", "/favicon.ico"})

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.QUIET_PATHS:
            return await call_next(request)
        start = time.perf_counter()
        client_ip = resolve_client_ip(request)
        status_code = 500
        response = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally: # type: ignore
            duration_ms = (time.perf_counter() - start) * 1000
            request_id = getattr(request.state, "request_id", "unknown")
            if status_code >= 500:
                log_fn = logger.error
            elif status_code >= 400:
                log_fn = logger.warning
            else:
                log_fn = logger.info # type: ignore
            if os.getenv("LOG_FORMAT", "text").lower() == "json":
                log_fn(
                    json.dumps(
                        {
                            "event": "http_access",
                            "request_id": request_id,
                            "method": request.method,
                            "path": request.url.path,
                            "status": status_code,
                            "duration_ms": round(duration_ms, 2),
                            "client_ip": client_ip,
                        }
                    )
                )
            else:
                log_fn(
                    "%s | ACCESS | %s %s | status=%s | %.1fms | ip=%s",
                    request_id,
                    request.method,
                    request.url.path,
                    status_code,
                    duration_ms,
                    client_ip,
                )


class SecurityMiddleware(BaseHTTPMiddleware):
    """Block known malicious IPs and scanner paths before auth/rate-limit chains."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        is_noisy = any(path.startswith(p) for p in NOISY_PATHS)
        request.state.is_noisy = is_noisy
 # type: ignore
        if is_noisy:
            return JSONResponse(status_code=403, content={"error": "Forbidden"})

        if BLOCKED_IPS:
            client_ip = resolve_client_ip(request)
            if client_ip in BLOCKED_IPS:
                return JSONResponse(status_code=403, content={"error": "Forbidden"})

        return await call_next(request)


class ProcessingTimeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.perf_counter()
        response = await call_next(request)
        process_time = time.perf_counter() - start_time
        response.headers["X-Processing-Time"] = f"{process_time:.4f}"
        return response

class SamplingMiddleware(BaseHTTPMiddleware):
    """Sample a fraction of requests for detailed performance profiling.""" # type: ignore
    SAMPLE_RATE = float(os.getenv("REQUEST_SAMPLE_RATE", "0.05"))

    async def dispatch(self, request: Request, call_next):
        should_sample = random.random() < self.SAMPLE_RATE
        if should_sample:
            request.state.should_sample = True
            start = time.perf_counter()
            response = await call_next(request)
            duration = time.perf_counter() - start
            request_id = getattr(request.state, "request_id", "unknown")
            logger.info(
                f"SAMPLE | {request_id} | {request.method} {request.url.path} | "
                f"{duration:.3f}s | {response.status_code}"
            )
            return response
        return await call_next(request)

class KYCGraceHeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception as e:
            logger.error(f"SYSTEM | KYC Middleware caught: {e}", exc_info=True)
            return JSONResponse(status_code=500, content={"error": "Internal server error in KYC chain"})
            
        if getattr(request.state, "kyc_grace_mode", False) and response.status_code < 400:
            response.headers["X-KYC-Mode"] = "grace"

            user_quota = (
                getattr(request.state, "identity", {}).get("quota_details")
                or getattr(request.state, "user", {}).get("quota_details") # type: ignore
                or {}
            )
            limit = int(user_quota.get("limit") or os.getenv("KYC_GRACE_MAX_TOKENS", "4096"))
            if limit < 100:
                limit = 4096
            response.headers["X-KYC-Limit"] = str(limit)

            user = getattr(request.state, "user", {})
            identity_key = build_identity_key(user.get("user_id"), getattr(request.state, "api_key", None))

            used = getattr(request.state, "total_usage_after_request", None)
            if used is None and not isinstance(response, StreamingResponse):
                used = await get_user_usage(identity_key)
            if used is not None:
                response.headers["X-Tokens-Used"] = str(used)
                response.headers["X-Tokens-Remaining"] = str(max(0, limit - used))

        return response

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Capture Vertical Context from headers early for all requests
        count = 0
        vertical_id = request.headers.get("X-Vertical-ID", "core").strip().lower() # type: ignore
        request.state.vertical_id = vertical_id

        EXEMPT_PATHS = {
            "/", "/health", "/ready", "/models", "/v1/models",
            "/docs", "/openapi.json", "/metrics", "/redoc",
            "/ping", "/healthz", "/status", "/aarab/agents/capabilities",
        }

        if request.method == "OPTIONS" or request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        token = None
        source = "None"

        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "): # type: ignore
            token = auth_header.replace("Bearer ", "").strip()
            source = "Header"
        else:
            token = request.cookies.get("access_token")
            source = "Cookie" if token else "None"

        # 1. Internal Service Trust Boundary
        # In remote mode, we MUST have a valid internal secret to trust the upstream gateway (Production Hardening)
        internal_secret = request.headers.get("X-Internal-Secret") # type: ignore
        internal_service = (request.headers.get("X-Internal-Service", "") or "").strip().lower()
        is_background_service = (request.headers.get("X-Background-Service", "") or "").strip().lower() == "true"
        has_valid_internal_secret = bool(internal_secret and internal_secret == INTERNAL_SERVICE_SECRET)
        request_id = getattr(request.state, "request_id", "unknown")

        # Detect scanners early to suppress scary logs for unauthenticated bot probes
        is_noisy = any(request.url.path.startswith(p) for p in NOISY_PATHS)
        request.state.is_noisy = is_noisy # type: ignore

        if internal_secret and not is_noisy and request.url.path.startswith("/chat/history"):
            log_gateway_secret_event(
                logger,
                request_id=request_id,
                event="auth_internal_secret_check",
                configured_secret=INTERNAL_SERVICE_SECRET,
                header_secret=internal_secret,
                path=request.url.path,
                extra={
                    "env_key": _INTERNAL_SECRET_ENV_KEY,
                    "has_bearer": bool(token),
                    "internal_service": internal_service,
                },
            )

        # DEBUG ONLY: log header presence for trusted gateway diagnostics without trusting raw user claims.
        header_user_id = request.headers.get("X-User-ID") # type: ignore
        logger.debug(
            "AUTH_DEBUG | Headers received: user_id=%s, verified=%s, kyc=%s",
            header_user_id,
            request.headers.get("X-User-Verified"),
            request.headers.get("X-User-KYC-Status"),
        )

        # Legacy header-based delegation is disabled for user identity assertions.
        # All user-facing requests must be validated via the auth service or a signed delegated worker JWT.
        if has_valid_internal_secret and header_user_id and header_user_id != "0":
            if not is_noisy:
                logger.debug( # type: ignore
                    "AUTH_DEBUG | Received internal identity headers for user_id=%s; header trust is disabled.",
                    header_user_id,
                )

        # Service-to-Service trust gate: strictly enforced for background workers or internal services.
        # Regular user tokens (checked later) can bypass this if not using delegation headers.
        if is_background_service and not has_valid_internal_secret:
            if not is_noisy:
                logger.warning(f"{request_id} | AUTH | Trust failure for background service '{internal_service}' | Mode: {settings.auth_mode}") # type: ignore
            return JSONResponse(status_code=403, content={"error": "Forbidden: Service-to-Service trust required"})

        elif internal_secret and not has_valid_internal_secret:
            # PHP gateway always forwards X-Internal-Secret. If it is stale or mismatched
            # with this node's env, still allow user Bearer JWT authentication.
            if not is_noisy:
                log_gateway_secret_event(
                    logger,
                    request_id=request_id,
                    event="internal_secret_mismatch",
                    configured_secret=INTERNAL_SERVICE_SECRET,
                    header_secret=internal_secret,
                    path=request.url.path,
                    extra={"has_bearer": bool(token), "env_key": _INTERNAL_SECRET_ENV_KEY},
                )
            if not token: # type: ignore
                if not is_noisy:
                    logger.error(f"{request_id} | AUTH | Service-to-Service trust failed: Invalid secret")
                return JSONResponse(status_code=403, content={"error": "Forbidden: Internal trust failure"})
            if not is_noisy:
                logger.warning(
                    f"{request_id} | AUTH | Invalid X-Internal-Secret from upstream; "
                    f"falling back to Bearer token validation"
                )

        # Trusted background services authenticate with the gateway secret only
        # when there is no Bearer token to validate. Research workers forward a
        # delegated user JWT plus internal headers; those requests must remain
        # attributed to the user for quota and usage accounting.
        if (
            has_valid_internal_secret
            and is_background_service
            and internal_service in TRUSTED_BACKGROUND_SERVICES
            and not token
        ):
            service_user = {
                "user_id": internal_service,
                "username": internal_service,
                "role": "system",
                "roles": ["system"],
                "scopes": ["internal", "chat", "embeddings"],
                "kyc_status": "verified",
                "subscription": {"status": "active"},
            }
            request.state.api_key = f"internal:{internal_service}"
            request.state.user = service_user
            request.state.identity = {
                "user": service_user,
                "access_token": None,
                "csrf_token": None,
            }
            request.state.access_token = None
            request.state.csrf_token = None
            
            # Ensure organization ID is tracked for internal services if provided
            org_id = request.headers.get("X-Organization-ID") # Use X-Organization-ID from header # type: ignore
            request.state.legal_organization_id = str(org_id) if org_id and org_id.isdigit() else "0"
            
            # Capture Vertical Context from headers
            vertical_id = request.headers.get("X-Vertical-ID", "general").strip().lower() # type: ignore
            request.state.vertical_id = vertical_id
 # type: ignore
            logger.info(f"{request_id} | AUTH | Internal service authenticated: {internal_service} | Org: {request.state.legal_organization_id}")
            return await call_next(request)

        if not token:
            logger.warning(f"{request_id} | AUTH | No token found (source: {source})")
            return JSONResponse(status_code=401, content={"error": "Authentication required"})

        logger.debug(f"{request_id} | AUTH | Authenticating via {source} (Mode: {settings.auth_mode.upper()}) | Token length: {len(token)}") # type: ignore

        # Static API keys for internal services
        if token in API_KEYS:
            request.state.api_key = token
            request.state.user = {
                "user_id": "service",
                "role": "system",
                "scopes": ["metrics", "internal"]
            }
            return await call_next(request)

        # Delegated research workers carry a short-lived JWT minted by the PHP
        # app, not a persisted browser access token. Validate those locally only
        # inside the service-to-service trust boundary.
        if (
            has_valid_internal_secret
            and is_background_service
            and internal_service in TRUSTED_BACKGROUND_SERVICES
        ):
            delegated_identity = decode_delegated_worker_token(token)
            if delegated_identity is not None: # If delegated token is valid # type: ignore
                request.state.identity = delegated_identity
                request.state.api_key = token
                request.state.user = delegated_identity.get("user", {})
                request.state.access_token = token
                request.state.csrf_token = None
                
                # Track Org ID from headers even for delegated tokens
                org_id = request.headers.get("X-Organization-ID") # Use X-Organization-ID from header
                request.state.legal_organization_id = str(org_id) if org_id and org_id.isdigit() else "0"
                
                logger.info(
                    f"{request_id} | AUTH | Delegated worker JWT accepted | "
                    f"service={internal_service} | user_id={request.state.user.get('user_id')}"
                )
                return await call_next(request)

        # External identity verification
        fresh_token = None
        try:
            identity = await fetch_user_profile(token, request.cookies, request=request) # type: ignore
            
            request.state.identity = identity # Store full identity for quota details
            fresh_token = identity.get("_fresh_access_token")
            request.state.api_key = fresh_token or token
            request.state.user = identity.get("user", {})
            request.state.access_token = identity.get("access_token")
            request.state.csrf_token = identity.get("csrf_token")

        except HTTPException as e:
            if e.status_code == 401:
                auth_mismatch_counter.labels(source=source).inc()
                logger.error( # type: ignore
                    f"{request_id} | AUTH | 401 Mismatch via {source} | "
                    f"IP: {request.client.host} | "
                    f"UA: {request.headers.get('user-agent', 'none')[:50]}... | "
                    f"Detail: {e.detail}"
                )
            return JSONResponse(status_code=e.status_code, content={"error": e.detail})

        # CSRF check only for cookie-based sessions (not Bearer)
        if (request.state.csrf_token and # CSRF check (Production Hardening)
            source == "Cookie" and
            not request.headers.get("Authorization") and
            request.method not in ("GET", "HEAD", "OPTIONS", "TRACE")): # type: ignore

            csrf_header = request.headers.get("X-CSRF-TOKEN")
            if not csrf_header or csrf_header != request.state.csrf_token:
                logger.warning(f"{request_id} | AUTH | CSRF check failed")
                return JSONResponse(status_code=403, content={"error": "Invalid CSRF token"})

        response = await call_next(request)
        if fresh_token:
            response.headers["X-New-Access-Token"] = fresh_token
        return response

class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Prefer stable user identifier for more accurate rate limiting
        # Fallback to client IP for anonymous requests for better DDoS protection
        user = getattr(request.state, "user", {})

        # Unauthenticated global cap (DDoS Protection) - 30 req/min
        effective_limit = settings.rate_limit if (isinstance(user, dict) and user.get("user_id")) else 30

        if isinstance(user, dict) and user.get("user_id"): # Authenticated user
            # FIX: Use ONLY user_id for authenticated users to avoid NAT/VPN collisions
            raw_key = f"u:{user['user_id']}"
        else:
            client_ip = resolve_client_ip(request)
            raw_key = f"anon:{client_ip}"
            
        key = rate_limit_key(raw_key) # type: ignore
        rid = getattr(request.state, "request_id", "unknown")
        
        # Determine if path is noisy (to reduce log spam)
        is_noisy = getattr(request.state, "is_noisy", any(request.url.path.startswith(p) for p in NOISY_PATHS))
        
        # 1. Redis Rate Limiting (Primary - Shared across workers)
        if redis_client:
            async def _rl_op():
                rl_key = f"rl:{key}:{int(time.time() // 60)}" # type: ignore
                count = await redis_client.incr(rl_key)
                if count == 1:
                    await redis_client.expire(rl_key, 60)
                return count

            count = await safe_redis_op(_rl_op())
            if count is not None and count > effective_limit:
                if not is_noisy:
                    logger.warning(
                        f"{rid} | SYSTEM | RATE_LIMIT | Path: {request.url.path} | user_id={user.get('user_id', 'anon')} | key={key[:4]}... exceeded quota (shared)"
                    )
                return JSONResponse(status_code=429, content={"error": "Rate limit exceeded - please wait"})

        # 2. Local Fallback (Secondary - Safety if Redis is down)
        now = time.time()
        async with rate_limit_lock:
            window = rate_limit_store[key]
            while window and now - window[0] >= 60:
                window.popleft()

            if len(window) >= effective_limit:
                if not is_noisy:
                    logger.warning(
                        f"{rid} | SYSTEM | RATE_LIMIT | Path: {request.url.path} | user_id={user.get('user_id', 'anon')} | key={key[:4]}... exceeded quota (local)"
                    )
                return JSONResponse(status_code=429, content={"error": "Rate limit exceeded - please wait 60s"})

            # Global DDoS protection: Hard cap on anonymous requests
            if raw_key.startswith("anon:") and len(window) > (RATE_LIMIT // 2):
                if not is_noisy:
                    logger.warning(
                        f"{rid} | SYSTEM | RATE_LIMIT | Path: {request.url.path} | user_id=anon | key={key[:4]}... exceeded quota (Anonymous local)"
                    )
                return JSONResponse(status_code=429, content={"error": "Rate limit exceeded"})

            window.append(now)  # Add current timestamp

        try:
            return await call_next(request) # type: ignore
        except Exception as e:
            logger.error(f"{rid} | RATE_LIMIT | Middleware crash: {e}", exc_info=True)
            return JSONResponse(status_code=500, content={"error": "Internal rate limit error"})

class AdmissionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = getattr(request.state, "request_id", "unknown")

        # Bypass admission control for preflight, static assets and health endpoints
        if (request.method == "OPTIONS" or
            request.url.path in {"/", "/health", "/healthz", "/ping", "/ready", "/status",
                                 "/metrics", "/docs", "/redoc", "/openapi.json"} or
            request.url.path.startswith(("/css", "/js", "/img"))):
            return await call_next(request) # type: ignore

        # CRITICAL: Early rejection if inference is not ready
        if ollama_client is None or not readiness_state.get("llm", False):
            logger.error(f"{request_id} | SYSTEM | Rejecting {request.url.path} - Inference engine not ready")
            return JSONResponse( # type: ignore
                status_code=503,
                content={"error": "Inference engine still initializing or warming up"}
            )

        acquired = False
        try:
            await asyncio.wait_for(global_admission_semaphore.acquire(), timeout=1.5)
            acquired = True
            set_global_admission_metrics() # type: ignore
        except asyncio.TimeoutError:
            set_global_admission_metrics()
            logger.warning(f"{request_id} | SYSTEM | Admission Control: Server overloaded")
            return JSONResponse(
                status_code=503,
                content={"error": "Server overloaded - try again later"}
            )

        try:
            return await call_next(request)
        except Exception as e: # type: ignore
            logger.error(f"{request_id} | ADMISSION | App crash: {e}", exc_info=True)
            return JSONResponse(status_code=500, content={"error": "Inference application error"})
        finally:
            if acquired:
                global_admission_semaphore.release()
                set_global_admission_metrics()

# ============================================================
# MIDDLEWARE REGISTRATION
# ============================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://arybit.co.ke",
        "https://core6.arybit.co.ke",
        "https://api.arybit.co.ke",
        "https://account.arybit.co.ke",
        "https://aarab.arybit.co.ke",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:8080",
        "http://127.0.0.1:8080",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=7200,
)
app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(AdmissionMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(KYCGraceHeaderMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(SecurityMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(ProcessingTimeMiddleware)
app.add_middleware(SlowQueryMiddleware)
app.add_middleware(SamplingMiddleware)
app.add_middleware(AccessLogMiddleware)

# ============================================================
# CIRCUIT BREAKER IMPLEMENTATION
# ============================================================
class CircuitBreaker:
    def __init__(self, model: str = "unknown", threshold: int = 5, reset_timeout: int = 30):
        self.model = model
        self.failures = 0
        self.last_failure = 0.0
        self.threshold = threshold
        self.reset_timeout = reset_timeout
        self.lock = asyncio.Lock()

    async def check_state(self):
        async with self.lock:
            if self.failures >= self.threshold:
                time_since_failure = time.time() - self.last_failure
                if time_since_failure < self.reset_timeout:
                    raise HTTPException( # type: ignore
                        status_code=503, 
                        detail=f"Circuit open. Cooling down for {int(self.reset_timeout - time_since_failure)}s"
                    )
                else:
                    # Half-open: allow probe
                    logger.info("Circuit Breaker half-open: probing Ollama recovery...")
                    self.failures = self.threshold - 1 # Allow one more attempt

    async def record_success(self):
        async with self.lock:
            if self.failures > 0:
                logger.info("Circuit Breaker: Success detected, resetting failures.")
            self.failures = 0

    async def record_failure(self):
        async with self.lock:
            self.failures += 1
            self.last_failure = time.time()
            circuit_breaker_gauge.labels(model=self.model).set(1) # Update gauge to open state
            logger.warning(f"Circuit Breaker: Failure recorded. Total failures: {self.failures}")

CIRCUIT_BREAKER_CONFIGS = {
    "llama3.2:3b": {"threshold": 12, "reset_timeout": 45},  # Increased threshold for CPU spikes
    "llama3.2:1b": {"threshold": 5, "reset_timeout": 30},
    "phi3:mini": {"threshold": 6, "reset_timeout": 45},
}

circuit_breakers = {}
for model in ALLOWED_MODELS:
    cfg = CIRCUIT_BREAKER_CONFIGS.get(model, {"threshold": 5, "reset_timeout": 30}) # type: ignore
    circuit_breakers[model] = CircuitBreaker(model=model, threshold=cfg["threshold"], reset_timeout=cfg["reset_timeout"])

auth_circuit_breaker = CircuitBreaker(model="auth", threshold=5, reset_timeout=30)

# ============================================================
# SMART MODEL ROUTER
# ============================================================
class ModelRouter:
    """Intelligent request routing based on prompt content and real-time model load."""

    def __init__(self):
        self.fallback_chain = {
            "llama3.2:3b": ["phi3:mini", "llama3.2:1b"],
            "llama3.2:3b": ["phi3:mini", "llama3.2:1b"], # Prefer phi3:mini for quality/speed balance
            "phi3:mini": ["llama3.2:1b"],               # 1b is the ultra-fast safety net
            "llama3.2:1b": ["phi3:mini"],
            "phi3:mini": ["llama3.2:1b"],
        }

    async def select_model(self, body: ChatRequest) -> str:
        """CPU-optimized routing: phi3:mini is the default; Llama models are optional fallbacks."""
        if body.model:
            return body.model

        async with active_requests_lock: # type: ignore
            active_phi3 = active_requests_by_model.get("phi3:mini", 0) # type: ignore

        limit_phi3 = MODEL_CONCURRENCY["phi3:mini"]
        if active_phi3 >= max(limit_phi3 - 1, 1) and limit_phi3 > 1:
            return "llama3.2:1b"

        return LLM_MODEL

router = ModelRouter()

class QueryClassifier:
    """Classifies queries to determine if they can use faster, smaller models."""
    @staticmethod
    def is_complex(text: str) -> bool:
        text = text.lower()
        complex_keywords = [
            "analyze", "reason", "evaluate", "compare", "structured",
            "legal", "regulation", "statute", "calculate", "code"
        ]
        # Long prompts or specific keywords trigger the heavy-duty model
        if len(text.split()) > 50: return True
        return any(k in text for k in complex_keywords)

query_classifier = QueryClassifier()

# ============================================================
# OPENAI COMPATIBILITY MODELS
# ============================================================
class OpenAIChatRequest(BaseModel):
    model: str
    messages: List[ChatMessage]
    stream: Optional[bool] = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 256

class OpenAIUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

class OpenAIChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: Optional[str] = "stop"

class OpenAIChatResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[OpenAIChoice]
    usage: OpenAIUsage

class OpenAIDelta(BaseModel):
    content: Optional[str] = None

class OpenAIStreamChoice(BaseModel):
    index: int
    delta: OpenAIDelta
    finish_reason: Optional[str] = None

# ============================================================
# HELPERS
# ============================================================
async def log_ai_usage(user_id: str, model: str, tokens_used: int, request_type: str = "chat", request_id: str = "unknown"):
    """Log usage to the PHP backend's ai_usage_log table via a simple internal call."""
    if not auth_internal_client:
        return # type: ignore

    # Production Fix: Robustly extract numeric ID from namespaced keys (e.g., org:1:user:57)
    external_user_id = user_id
    id_match = re.search(r'user:(\d+)', str(user_id))
    if id_match:
        external_user_id = id_match.group(1)

    if not str(external_user_id).isdigit():
        logger.debug(
            "Skipping usage log for system/anonymous identity %s | model=%s",
            user_id,
            model,
            tokens_used,
        )
        return

    try:
        resp = await auth_internal_client.post( # type: ignore
            "/authenticate/internal/log-ai-usage",
            json={
                "user_id": external_user_id,
                "identity_key": user_id,
                "model": model,
                "tokens_used": tokens_used,
                "request_type": request_type
            },
            headers={
                "X-Internal-Service": INTERNAL_SERVICE_NAME, 
                "X-Internal-Secret": INTERNAL_SERVICE_SECRET
            },
            timeout=httpx.Timeout(5.0)   # Short timeout for background logging
        )
        if resp.status_code >= 400:
            log_ctx(
                request_id,
                "Failed to log AI usage",
                level="warning",
                user_id=user_id,
                model=model,
                tokens_used=tokens_used,
                status_code=resp.status_code,
                response_snippet=resp.text[:300],
            )
    except Exception as e:
        logger.warning(f"Failed to log AI usage for user {user_id} | model={model} | tokens={tokens_used}: {e}")

async def prepare_chat_context(api_key: str, user_id: Optional[str], body: ChatRequest, org_id: int = 0) -> tuple[str, str, List[dict]]:
    """Extract and prepare conversation ID, content, and history for LLM processing."""
    identity_key = build_identity_key(user_id, api_key, str(org_id) if org_id else None)
    conversation_id = await get_conversation_id(identity_key, body.conversation_id, org_id)
    incoming_messages: List[dict] = [] # type: ignore
    if body.messages:
        incoming_messages = [m.model_dump() for m in body.messages]
    elif body.prompt:
        incoming_messages = [{"role": "user", "content": body.prompt}]
    user_content = incoming_messages[-1]["content"] if incoming_messages else ""
    full_messages = await build_messages_with_memory(
        identity_key, conversation_id, incoming_messages, body.max_memory_messages, org_id
    )
    return conversation_id, user_content, full_messages

async def get_cached_prompt_response(model: str, messages: List[dict], vertical: str) -> Optional[dict]:
    """Redis-backed prompt caching for common queries."""
    if not redis_client:
        return None
    
    # Create hash of model, vertical, and messages # type: ignore
    payload_str = f"{model}:{vertical}:{json.dumps(messages, sort_keys=True)}"
    cache_key = f"prompt_cache:{hashlib.sha256(payload_str.encode()).hexdigest()}"
    
    cached = await safe_redis_op(redis_client.get(cache_key))
    if cached:
        logger.info(f"CACHE | Hit for key {cache_key[:8]}")
        return json.loads(cached)
    return None

async def set_prompt_cache(model: str, messages: List[dict], vertical: str, response: dict, ttl: int = 3600):
    """Store response in Redis cache."""
    if not redis_client:
        return
    payload_str = f"{model}:{vertical}:{json.dumps(messages, sort_keys=True)}" # type: ignore
    cache_key = f"prompt_cache:{hashlib.sha256(payload_str.encode()).hexdigest()}"
    await safe_redis_op(redis_client.setex(cache_key, ttl, json.dumps(response)))

async def _perform_chat_logic(
    request: Request,
    body: ChatRequest,
    user: dict,
    request_id: str,
    format: Optional[str] = None # New: Add format parameter for JSON output
) -> ChatResponse:
    """Internal reusable chat logic that bypasses redundant auth dependencies.""" # type: ignore
    if ollama_client is None:
        raise HTTPException(status_code=503, detail="Inference engine still initializing")

    base_model = body.model or await router.select_model(body)
    validate_model(base_model)

    vertical = getattr(request.state, "vertical_id", "core")
    collection_name = get_vector_collection(vertical)

    api_key = getattr(request.state, "api_key", "anonymous")
    user_id = user.get("user_id") if isinstance(user, dict) else None
    org_id = int(getattr(request.state, "legal_organization_id", 0))
    conversation_id, user_content, full_messages = await prepare_chat_context(api_key, user_id, body, org_id)
    # Query Classification Path: if model not explicitly requested and the user's
    # content is simple, prefer a lightweight low-latency model.
    if not body.model and not query_classifier.is_complex(user_content):
        base_model = "llama3.2:1b"
    identity_key = build_identity_key(user_id, api_key, str(org_id) if org_id else None)

    # 3. Dynamic Context Size
    is_document_analysis = len(user_content) > 3000 or "X-Task-ID" in request.headers
    if is_document_analysis: # type: ignore
        num_ctx = 8192  # Full context for documents
        max_output = 2048  # Allow longer responses
    else:
        num_ctx = 2048  # Half the context for chat (4x faster)
        max_output = 512  # Shorter responses for chat

    # 1. Check Cache
    cached_res = await get_cached_prompt_response(base_model, full_messages, vertical)
    if cached_res:
        return ChatResponse(
            model=base_model, response=cached_res["response"], done=True,
            conversation_id=conversation_id, **cached_res.get("meta", {})
        )

    # 5. Skip RAG for Simple Chat
    is_simple_chat = len(user_content) < 200 and not any(k in user_content.lower() for k in ["document", "pdf", "file", "analyze"])
    retrieved_context = "" # type: ignore
    if not is_simple_chat and qdrant_client and user_content and len(user_content.split()) > 5:
        try:
            query_vector = await create_embedding(user_content)
            if query_vector:
                search_results = await qdrant_client.search(
                    collection_name=collection_name,
                    query_vector=query_vector,
                    query_filter=models.Filter(
                        must=[models.FieldCondition(key="org_id", match=models.MatchValue(value=org_id))] # type: ignore
                    ),
                    limit=3
                )
                if search_results:
                    retrieved_context = "\n".join([hit.payload.get("text", "") for hit in search_results])
                    # Inject retrieved context into the system/first message with vertical awareness
                    rag_instruction = f"\n\nRelevant {vertical.replace('_', ' ').capitalize()} Context:\n{retrieved_context}\n"
                    if full_messages and full_messages[0]["role"] == "system":
                        full_messages[0]["content"] += rag_instruction
                    else:
                        system_prompt = await get_cached_system_prompt(vertical)
                        full_messages.insert(0, {"role": "system", "content": f"{system_prompt} {rag_instruction}"})
        except Exception as e:
            logger.warning(f"{request_id} | QDRANT | Vector search unavailable: {e}. Continuing without RAG context.")

    fallbacks = router.fallback_chain.get(base_model, [])
    models_to_try = [m for m in [base_model] + fallbacks if m in settings.allowed_models]
    
    span_ctx = tracer.start_as_current_span("chat_inference") if tracer else None
    if span_ctx is None:
        from contextlib import nullcontext
        span_ctx = nullcontext()

    with span_ctx:
        # Vertical Override Logic
        v_config = get_vertical_config(getattr(request.state, "vertical_id", "core"))
        temp = body.temperature if body.temperature != 0.7 else v_config.get("temperature", 0.7)
 # type: ignore
        last_error: Optional[Exception] = None
        # V10.9.272: Lower retries for heavy research tasks to stay within gateway/proxy limits
        inference_retries = int(os.getenv("INFERENCE_RETRIES", "1"))

        # Safety: Validate max_tokens against model context floor
        safe_max_tokens = min(body.max_tokens or 1024, DEFAULT_NUM_CTX)
        if (body.max_tokens or 0) > DEFAULT_NUM_CTX:
            logger.warning(f"{request_id} | CLIP | max_tokens {body.max_tokens} exceeds context {DEFAULT_NUM_CTX}")

        for model in models_to_try:
            try:
                await get_circuit_breaker(model).check_state() # type: ignore
                async with get_model_semaphore(model), RequestTracker(RequestType.CHAT, model):
                    logger.info(f"{request_id} | {model} | Starting inference | payload≈{len(str(full_messages))} chars")
                    start_time = time.perf_counter()
                    ollama_resp = await retry_ollama_request(
                        "POST", "/api/chat", model_name=model, max_retries=inference_retries,
                        json={
                            "model": model, "messages": full_messages, "stream": False, "keep_alive": "5m", "format": format,
                            "options": {
                                "temperature": temp, # type: ignore
                                "num_predict": min(body.max_tokens or 1024, max_output),
                                "num_ctx": num_ctx
                            },
                        }, 
                        timeout=REQUEST_TIMEOUT
                    )
                    data = ollama_resp.json()
                    duration = time.perf_counter() - start_time
                    
                    assistant_response = data.get("message", {}).get("content", "")
                    await update_conversation_history(identity_key, conversation_id, user_content, assistant_response, org_id)
                    await record_actual_usage(
                        identity_key=identity_key, model=model, prompt_eval_count=data.get("prompt_eval_count"),
                        eval_count=data.get("eval_count"), request_type="chat", request=request,
                    )

                    return ChatResponse(
                        model=model, response=assistant_response, done=data.get("done", False), 
                        conversation_id=conversation_id, context=data.get("context"),
                        total_duration=data.get("total_duration"), prompt_eval_count=data.get("prompt_eval_count"),
                        eval_count=data.get("eval_count")
                    )
            except Exception as e:
                logger.warning(f"{request_id} | {model} | Attempt failed: {str(e)}")
                last_error = e
                continue

    raise HTTPException(
        status_code=503,
        detail=f"All models failed. Last error: {str(last_error) if last_error else 'Unknown'}"
    )

# ============================================================
# SHARED STREAMING HELPER
# ============================================================
async def _internal_ollama_stream_generator(
    base_model: str,
    full_messages: List[dict],
    body: StreamingChatRequest,
    request_id: str,
    identity_key: str,
    conversation_id: str,
    user_content: str,
    request: Request,
    org_id: int = 0,
    format: Optional[str] = None, # New: Add format parameter for JSON output
    agent_info: Optional[dict] = None  # NEW: Agent information
):
    """Reusable streaming logic for both native and OpenAI endpoints."""
    if not ollama_client:
        yield {"type": "error", "error": "Inference client not initialized"}
        return

    # Initialize keep-alive timer
    last_keep_alive = time.time()

    # Immediate status yield to keep the socket alive during CPU prefill
    if agent_info:
        agent_name = agent_info.get('name', agent_info.get('agent_type', 'AI Agent'))
        agent_action = agent_info.get('current_action', 'Analyzing context')
        yield {
            "type": "status",
            "content": f"{agent_name} is {agent_action}...",
            "agent": agent_name,
            "agent_type": agent_info.get('agent_type'),
            "step": "initializing",
            "progress": 5
        }
        await asyncio.sleep(0.01)
        
        # Simulate prefill progress updates for CPU prefill duration
        for progress in [15, 25, 35, 45]:
            yield {
                "type": "status",
                "content": f"{agent_name} is processing contextual tokens...",
                "agent": agent_name,
                "step": "prefill",
                "progress": progress
            }
            await asyncio.sleep(0.5) 
    else:
        yield {"type": "status", "content": "Initializing intelligence orchestration..."}

    await asyncio.sleep(0.01)

    assistant_response_content = ""
    is_doc = len(user_content) > 3000 or "X-Task-ID" in request.headers
    vertical_id = getattr(request.state, "vertical_id", "core")

    # AARAB still needs room for the vertical system message; 1024 ctx often yields empty CPU output.
    if is_doc:
        num_ctx = 8192
    elif vertical_id == "aarab":
        num_ctx = 2048
    else:
        num_ctx = 1024 if len(user_content) < 500 else 2048
    
    current_timeout_val = get_stream_timeout(len(user_content), has_file=is_doc)
    streaming_timeout = httpx.Timeout(connect=10.0, read=current_timeout_val, write=30.0, pool=10.0)

    v_config = get_vertical_config(getattr(request.state, "vertical_id", "core")) # type: ignore
    temp = body.temperature if body.temperature != 0.7 else v_config.get("temperature", 0.7)

    fallbacks = router.fallback_chain.get(base_model, [])
    models_to_try = [m for m in [base_model] + fallbacks if m in ALLOWED_MODELS]

    inference_retries = 0 # No retries for streaming to avoid broken pipes

    # Final safety clamp for output tokens to ensure prompt + completion fits in context
    num_predict = min(body.max_tokens or 1024, 2048 if is_doc else 512)

    for attempt_model in models_to_try:
        try:
            await get_circuit_breaker(attempt_model).check_state() # type: ignore
            async with get_model_semaphore(attempt_model), RequestTracker(RequestType.CHAT, attempt_model):
                logger.info(f"{request_id} | INFERENCE | Starting stream generation | model={attempt_model} | max_tokens={num_predict} | prompt_tokens≈{len(str(full_messages))}")

                async with ollama_client.stream(
                    "POST", "/api/chat", 
                    json={
                        "model": attempt_model, "format": format,
                        "messages": full_messages,
                        "stream": True,
                        "keep_alive": "5m",
                        "options": {
                            "temperature": temp, # type: ignore
                            "num_predict": num_predict,
                            "num_ctx": num_ctx
                        },
                    },
                    timeout=streaming_timeout
                ) as resp:
                    resp.raise_for_status()
                    
                    # Reset keep-alive for each new model attempt
                    last_keep_alive = time.time()

                    line_iter = resp.aiter_lines().__aiter__()
                    while True:
                        try:
                            line = await asyncio.wait_for( # type: ignore
                                line_iter.__anext__(),
                                timeout=STREAM_LINE_POLL_SEC,
                            )
                        except asyncio.TimeoutError:
                            # Step 4: Chunked Prefill Progress
                            # This keeps the connection alive during long CPU compute
                            current_progress = min(90, 45 + (int(time.time() - last_keep_alive) // 10) * 5)
                            yield {
                                "type": "status",
                                "content": "Orchestrating logical shards... please hold.",
                                "step": "prefill_compute",
                                "progress": current_progress
                            }
                            yield {
                                "type": "status",
                                "content": "Model is processing on CPU (prefill) — please wait…",
                                "step": "prefill",
                            }
                            continue
                        except StopAsyncIteration:
                            break

                        if not line or not line.strip().startswith("{"):
                            continue
                        saw_first_line = True
                        try:
                            payload = json.loads(line.strip())
                        except json.JSONDecodeError:
                            continue

                        if "message" in payload:
                            token = payload["message"].get("content", "")

                            # Identity Sanitizer: Improved regex-based replacement for robustness
                            if any(x in token.lower() for x in ["developed by", "created by", "as an ai"]): # type: ignore
                                token = re.sub(r"(?i)as an ai developed by (microsoft|openai|google)", "as AARAB by Arybit", token)
                                token = re.sub(r"(?i)as an ai, i", "as AARAB, i", token)
                                token = re.sub(r"(?i)i am an ai", "i am AARAB", token)
                                token = re.sub(r"(?i)i'm an ai", "i'm AARAB", token)

                            assistant_response_content += token
                            yield {"type": "token", "token": token}

                        if payload.get("done"):
                            if not assistant_response_content.strip():
                                logger.warning(
                                    f"{request_id} | {attempt_model} | Stream done with empty content | "
                                    f"eval_count={payload.get('eval_count')} prompt_eval={payload.get('prompt_eval_count')}"
                                )
                            await record_actual_usage(
                                identity_key=identity_key,
                                model=attempt_model,
                                prompt_eval_count=payload.get("prompt_eval_count"),
                                eval_count=payload.get("eval_count"),
                                request_type="chat_stream",
                                request=request,
                            )

                            await update_conversation_history(identity_key, conversation_id, user_content, assistant_response_content, org_id)

                            identity = getattr(request.state, "identity", {}) or {}
                            state_user = getattr(request.state, "user", {}) or {}
                            user_quota = identity.get("quota_details") or state_user.get("quota_details") or {} # type: ignore
                            grace_limit = int(user_quota.get("limit") or os.getenv("KYC_GRACE_MAX_TOKENS", "4096"))
                            if grace_limit < 100:
                                grace_limit = 4096

                            await get_circuit_breaker(attempt_model).record_success()
                            yield {
                                "type": "done",
                                "usage": {
                                    "prompt_eval_count": payload.get("prompt_eval_count"),
                                    "eval_count": payload.get("eval_count"),
                                    "total_duration": payload.get("total_duration"),
                                    "tokens_used": getattr(request.state, "tokens_used_this_request", 0),
                                    "total_used": getattr(request.state, "total_usage_after_request", 0),
                                    "remaining": max(0, grace_limit - getattr(request.state, "total_usage_after_request", 0)),
                                    "limit": grace_limit,
                                }
                            }
                            return # Finished successfully

                        if await request.is_disconnected():
                            logger.info(f"{request_id} | Client disconnected mid-stream")
                            raise asyncio.CancelledError()

                        # Send keep-alive every 15 seconds
                        current_time = time.time()
                        if current_time - last_keep_alive > 15:
                            yield {"type": "keep_alive"}
                            last_keep_alive = current_time

                        await asyncio.sleep(0)
        except asyncio.CancelledError: # type: ignore
            log_ctx(request_id, "Stream cancelled by client", level="info")
            return
        except httpx.ReadTimeout:
            logger.warning(f"{request_id} | {attempt_model} timeout after {current_timeout_val}s")
            # Step up timeout for fallback model
            current_timeout_val = min(current_timeout_val * 1.5, 900.0)
            streaming_timeout = httpx.Timeout(connect=10.0, read=current_timeout_val, write=30.0, pool=10.0)
            await get_circuit_breaker(attempt_model).record_failure()
            continue
        except Exception as e: # type: ignore
            logger.warning(f"{request_id} | {attempt_model} stream failed: {e}")
            # Don't record failure if the error is essentially a client disconnect
            if "broken pipe" not in str(e).lower() and "connection reset" not in str(e).lower():
                await get_circuit_breaker(attempt_model).record_failure()
            continue

    yield {"type": "error", "error": "All available models failed to generate a response. Please try again later."}

# ============================================================
# HELPERS & VALIDATION
# ============================================================
def get_current_user(request: Request) -> dict:
    """Safe accessor for the authenticated user from request.state."""
    user = getattr(request.state, "user", None)
    if not isinstance(user, dict): # type: ignore
        request_id = getattr(request.state, "request_id", "unknown")
        logger.warning(f"{request_id} | AUTH | No valid user in request.state")
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


async def enforce_user_access(request: Request):
    """Final hardened version with explicit is_verified priority."""
    user = get_current_user(request)
    request_id = getattr(request.state, "request_id", "unknown") # type: ignore
    path = request.url.path.rstrip('/')

    user_id = user.get("user_id")
    kyc_status = user.get("kyc_status", "pending").lower()
    is_verified = (
        bool(user.get("is_verified")) is True or
        kyc_status in {"verified", "approved", "verified_institutional"}
    )

    if user.get("role") == "system" or is_verified:
        logger.info(f"{request_id} | AUTH | Full access granted | user_id={user_id} | verified={is_verified}")
        return user

    # Pending users logic (read-only + grace)
    if kyc_status == "pending":
        allowed_read_only = {"/chat/history", "/models", "/v1/models", "/usage", "/grace-status", "/health", "/ready", "/status", "/ping", "/healthz", "/metrics"}

        if request.method == "GET" and (path in allowed_read_only or path.startswith("/chat/history")):
            return user

        if path in {"/documents/analyze", "/ingest", "/aarab/process"}:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "KYC_VERIFICATION_REQUIRED",
                    "message": "Document analysis requires full verification.",
                    "action": "https://account.arybit.co.ke/auth/verify-code"
                }
            )

        if request.method == "POST" and path in {"/chat", "/chat/stream", "/generate", "/v1/chat/completions", "/embeddings"}:
            return user

        raise HTTPException(status_code=403, detail="KYC verification required")

    return user

async def _apply_grace_limits(
    *, 
    prompt_length: int,
    estimated_tokens: int,
    user: dict,
    request: Request,
    model: Optional[str] = None
) -> None:
    is_verified = bool(user.get("is_verified")) or user.get("kyc_status") in ["verified", "approved", "verified_institutional"] # type: ignore
    
    if user.get("role") == "system" or is_verified: # Verified users bypass grace gates
        return

    # Model-class gate for grace mode users
    if model and model not in GRACE_ALLOWED_MODELS:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "Model requires full verification",
                "message": f"Model '{model}' is not available in grace mode. Please complete KYC verification for full access.",
                "action": "https://arybit.co.ke/account/kyc",
            },
        )
        return

    request_id = getattr(request.state, "request_id", "unknown")
    request_path = request.url.path
    identity_key = request_identity_key(request, user) # type: ignore

    # PRODUCTION REFINEMENT: Prefer dynamic limit from identity service over static .env
    identity = getattr(request.state, "identity", {})
    quota = identity.get("quota_details") or user.get("quota_details") or {}
    grace_max_tokens = int(quota.get("limit") or os.getenv("KYC_GRACE_MAX_TOKENS", "4096")) # Use dynamic limit or fallback
    grace_max_prompt_chars = int(os.getenv("KYC_GRACE_MAX_PROMPT_CHARS", "16000"))
    if grace_max_tokens < 100:
        grace_max_tokens = 4096 # type: ignore
    current_used = await get_user_usage(identity_key)

    log_ctx(
        request_id,
        "AUTH | Grace evaluation",
        user_id=user.get("user_id"),
        prompt_length=prompt_length,
        estimated_tokens=estimated_tokens,
        used_tokens=current_used,
        grace_limit=grace_max_tokens,
    )
    
    logger.info(f"{request_id} | GRACE | user={user.get('user_id')} | used={current_used}/{grace_max_tokens} | prompt={prompt_length} chars") # type: ignore

    if prompt_length > grace_max_prompt_chars:
        kyc_blocked_requests.labels(path=request_path, reason="prompt_too_long").inc()
        raise HTTPException(
            status_code=403,
            detail={
                "error": "Prompt too long for grace mode", # Specific error for prompt length
                "message": f"Pending verification accounts are limited to {grace_max_prompt_chars} prompt characters.",
                "limit": grace_max_prompt_chars,
                "action": "https://arybit.co.ke/account/kyc",
            },
        )

    if current_used + estimated_tokens > grace_max_tokens:
        log_ctx(
            request_id,
            "AUTH | Rejecting grace request - limit exceeded",
            level="warning",
            user_id=identity_key,
            used=current_used,
            requested=estimated_tokens,
            limit=grace_max_tokens
        )
        kyc_blocked_requests.labels(path=request_path, reason="limit_exceeded").inc()
        raise HTTPException(
            status_code=403,
            detail={
                "error": "KYC_VERIFICATION_REQUIRED",
                "message": f"Grace limit reached ({current_used}/{grace_max_tokens}).",
                "used": current_used,
                "limit": grace_max_tokens,
                "action": "https://account.arybit.co.ke/auth/verify-code",
            },
            headers={"X-KYC-Grace-Exceeded": "true"},
        )

    kyc_grace_requests.labels(path=request_path).inc() # Metric for grace requests
    request.state.kyc_grace_mode = True

async def enforce_chat_grace_limits(
    request: Request,
    body: ChatRequest,
):
    """Enforce grace limits for native chat-style generation endpoints.""" # type: ignore
    user = get_current_user(request)
    prompt_length = len(body.prompt or "")
    if body.messages:
        prompt_length += sum(len(m.content or "") for m in body.messages)

    prompt_text = body.prompt or " ".join((m.content or "") for m in body.messages or [])
    estimated_input = estimate_tokens(prompt_text)
    estimated_output = max(200, (body.max_tokens or 1024) // 2)
    estimated_tokens = estimated_input + estimated_output

    await _apply_grace_limits(
        prompt_length=prompt_length,
        estimated_tokens=estimated_tokens,
        user=user,
        request=request,
        model=body.model
    )

async def enforce_stream_grace_limits(
    request: Request,
    body: StreamingChatRequest,
):
    """Streaming uses the same grace logic as non-streaming chat."""
    await enforce_chat_grace_limits(request=request, body=body)
    
async def enforce_openai_grace_limits(
    request: Request,
    body: OpenAIChatRequest,
):
    """Enforce grace limits for OpenAI-compatible chat completions.""" # type: ignore
    user = get_current_user(request)
    prompt_length = sum(len(m.content or "") for m in body.messages)
    prompt_text = " ".join((m.content or "") for m in body.messages)
    estimated_input = estimate_tokens(prompt_text)
    estimated_output = max(128, (body.max_tokens or 256) // 2)
    estimated_tokens = estimated_input + estimated_output

    await _apply_grace_limits(
        prompt_length=prompt_length,
        estimated_tokens=estimated_tokens,
        user=user,
        request=request,
        model=body.model
    )

async def enforce_embeddings_grace_limits(
    request: Request,
    body: EmbeddingRequest,
):
    """Embeddings are lighter, so use prompt tokens plus a small fixed overhead.""" # type: ignore
    user = get_current_user(request)
    await _apply_grace_limits(
        prompt_length=len(body.text),
        estimated_tokens=estimate_tokens(body.text) + 20,
        user=user,
        request=request, # Embeddings don't have a model in body, so no model param here
    )

def _build_auth_proxy_headers(token: str, original_ip: str, original_ua: str, is_bot: bool) -> dict:
    """Helper to construct downstream headers for identity service calls."""
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": original_ua,
        "X-Requested-With": "XMLHttpRequest",
        "X-Real-IP": original_ip,
        "X-Forwarded-For": original_ip,
        "X-Forwarded-Proto": "https",
        "X-Forwarded-User-Agent": original_ua,
        "X-Original-IP": original_ip,
        "X-Original-UA": original_ua,
        "X-Original-User-Agent": original_ua,
        "X-Background-Service": "true" if is_bot else "false",
        "X-Service-Type": "arybit-autonomous-research-agent-bot" if is_bot else "gateway",
    }

def _resolve_client_context(request: Optional[Request]) -> tuple[str, str]:
    """Extract original IP and User-Agent from incoming request or defaults."""
    if request is None:
        return "127.0.0.1", "Mozilla/5.0 (ArybitAI-Backend/1.0)"

    xff = request.headers.get("x-forwarded-for") or request.headers.get("X-Forwarded-For")
    ip = (
        request.headers.get("x-original-ip")
        or request.headers.get("X-Original-IP")
        or request.headers.get("x-real-ip")
        or request.headers.get("X-Real-IP")
        or (xff.split(",")[0].strip() if xff else "")
        or getattr(request.client, "host", "unknown")
    )
    ua = (
        request.headers.get("x-original-ua")
        or request.headers.get("X-Original-UA")
        or request.headers.get("user-agent")
        or "ArybitAI-CurlClient/1.0"
    )
    return ip, ua

async def fetch_user_profile(token: str, cookies: dict | None = None, request: Optional[Request] = None) -> dict:
    """
    Final production-ready authentication proxy.
    Always presents as the trusted 'arybit-ai-gateway' to PHP while faithfully forwarding
    the original client IP/UA for perfect SessionBinder compatibility.
    """
    if not token or not token.strip():
        raise HTTPException(status_code=401, detail="No authentication token provided")

    token = token.strip()
    token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
    now = time.time()

    # L1: In-memory cache (fastest)
    async with auth_cache_lock:
        if token_hash in auth_cache:
            cached_data, expiry = auth_cache[token_hash]
            if now < expiry:
                auth_cache.move_to_end(token_hash)
                return cached_data

    # L2: Redis cache (graceful fallback)
    if redis_client:
        r_data = await safe_redis_op(redis_client.get(f"auth:{token_hash}"))
        if r_data:
            try:
                cached_data = json.loads(r_data)
                async with auth_cache_lock:
                    auth_cache[token_hash] = (cached_data, now + AUTH_CACHE_TTL)
                    auth_cache.move_to_end(token_hash)
                return cached_data
            except Exception:
                pass
    
    await auth_circuit_breaker.check_state()

    # Local JWT mode
    if settings.auth_mode == "local":
        try:
            if JWT_SECRETS:
                payload = decode_jwt_with_rotation(
                    token, algorithms=[JWT_ALGORITHM],
                    audience=["aarab-api", "arybit-gateway"],
                    issuer=["aarab-api", "arybit-auth"]
                )
            else:
                payload = jwt.decode(token, options={"verify_signature": False})

            user_data = payload.get("data", {}) or {}
            identity = {
                "user": {
                    "user_id": payload.get("sub"),
                    "username": user_data.get("username"),
                    "email": user_data.get("email"),
                    "phone": user_data.get("phone"),
                    "kyc_status": user_data.get("kyc_status", "verified"),
                    "subscription": user_data.get("subscription") or {"status": "active"},
                    "roles": user_data.get("roles", ["user"]),
                },
                "access_token": token,
                "csrf_token": None,
            }

            async with auth_cache_lock:
                if len(auth_cache) >= MAX_AUTH_CACHE_SIZE:
                    for _ in range(int(MAX_AUTH_CACHE_SIZE * 0.2)):
                        auth_cache.popitem(last=False)
                auth_cache[token_hash] = (identity, now + AUTH_CACHE_TTL * 2)
                auth_cache.move_to_end(token_hash)

            if redis_client:
                await safe_redis_op(redis_client.setex(f"auth:{token_hash}", AUTH_CACHE_TTL * 2, json.dumps(identity)))

            await auth_circuit_breaker.record_success()
            return identity
        except Exception as e:
            logger.warning(f"AUTH | Local JWT decode failed: {e}")
            raise HTTPException(status_code=401, detail="Invalid token")

    if auth_client is None:
        raise HTTPException(status_code=503, detail="Auth service not ready") # Auth client must be initialized

    # Determine context
    incoming_service = ((request.headers.get("X-Internal-Service", "").strip() if request else "") or "arybit-ai-gateway").lower()
    is_autonomous_bot = (
        incoming_service in TRUSTED_BACKGROUND_SERVICES
        or "research" in incoming_service.lower()
        or "autonomous" in incoming_service.lower()
    )

    original_ip, original_ua = _resolve_client_context(request)
    headers = _build_auth_proxy_headers(token, original_ip, original_ua, is_autonomous_bot)

    if not INTERNAL_SERVICE_SECRET:
        logger.critical("AUTH | Missing INTERNAL_SERVICE_SECRET")
        raise HTTPException(status_code=503, detail="Gateway misconfigured")

    try:
        logger.info(
            f"AUTH | /users/me → PHP | Effective=arybit-ai-gateway | "
            f"Incoming={incoming_service or 'direct'} | Bot={is_autonomous_bot} | "
            f"Original_IP={original_ip} | Original_UA={original_ua[:80]} | TokenLen={len(token)}"
        )

        resp = await auth_client.get(
            "/users/me",
            headers=headers,
            cookies=cookies or {},
            timeout=10.0
        )
        resp.raise_for_status()
        data = resp.json() # Parse response

        # Capture rotation token from identity service headers if present
        if "X-New-Access-Token" in resp.headers:
            data["_fresh_access_token"] = resp.headers["X-New-Access-Token"]

        # Cache success
        async with auth_cache_lock:
            if len(auth_cache) >= MAX_AUTH_CACHE_SIZE:
                for _ in range(int(MAX_AUTH_CACHE_SIZE * 0.2)):
                    auth_cache.popitem(last=False)
            auth_cache[token_hash] = (data, now + AUTH_CACHE_TTL)
            auth_cache.move_to_end(token_hash)

        if redis_client:
            await safe_redis_op(redis_client.setex(f"auth:{token_hash}", AUTH_CACHE_TTL, json.dumps(data)))

        await auth_circuit_breaker.record_success()
        
        # PRODUCTION FIX: Robust Organization ID detection
        user_data = data.get('user', {})
        header_org_id = None
        if request and request.headers.get("X-Internal-Secret") == INTERNAL_SERVICE_SECRET:
            header_org_id = request.headers.get("X-Organization-ID")

        profile_org_id = user_data.get("legal_organization_id")
        effective_org_id = "1"  # Default to Org 1 for production safety
        if header_org_id and header_org_id.isdigit() and int(header_org_id) > 0:
            effective_org_id = header_org_id
        elif profile_org_id and str(profile_org_id).isdigit() and int(str(profile_org_id)) > 0:
            effective_org_id = str(profile_org_id)
            
        request.state.legal_organization_id = str(effective_org_id)
        logger.info(f"AUTH | SUCCESS | user_id={user_data.get('user_id', 'unknown')} | org_id={effective_org_id} (Header: {header_org_id}, Profile: {profile_org_id})")
        return data

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            async with auth_cache_lock:
                auth_cache.pop(token_hash, None)

            detail = "Session expired or invalid. Please login again."
            try:
                php_error = e.response.json().get("error", "")
                if "expired" in php_error.lower() or "token" in php_error.lower():
                    detail = "Access token has expired. The worker needs a fresh token."
            except Exception:
                pass

            logger.warning(
                f"AUTH | 401 from PHP | Effective=arybit-ai-gateway | "
                f"IP={original_ip} | Bot={is_autonomous_bot} | "
                f"TokenLen={len(token)} | Detail: {detail}"
            )
            raise HTTPException(status_code=401, detail=detail)
        await auth_circuit_breaker.record_failure()
        raise HTTPException(status_code=502, detail="Auth service error") # Propagate HTTP errors

    except Exception as e:
        await auth_circuit_breaker.record_failure()
        logger.error(f"AUTH | Remote call failed: {e}")
        raise HTTPException(status_code=503, detail="Auth service unavailable")

def validate_model(model: str):
    """Ensures model is in allowed list."""
    if model not in settings.allowed_models:
        logger.warning(f"Unauthorized model rejected: {model}") # Log rejected models
        raise HTTPException(status_code=400, detail=f"Model '{model}' not allowed")

def get_model_semaphore(model_name: str) -> asyncio.Semaphore:
    """Returns a semaphore for the model."""
    if model_name not in model_semaphores:
        logger.error(f"SYSTEM | {model_name} | Semaphore requested but not initialized")
        raise HTTPException(status_code=400, detail=f"Inference state not initialized for model: {model_name}")
    return model_semaphores[model_name]

def get_circuit_breaker(model_name: str):
    """Returns a circuit breaker for the model."""
    if model_name not in circuit_breakers: # type: ignore
        logger.error(f"SYSTEM | {model_name} | Circuit breaker requested but not initialized")
        raise HTTPException(status_code=400, detail=f"Unknown model circuit breaker: {model_name}")
    return circuit_breakers[model_name]

# ============================================================
# CORE METADATA
# ============================================================
@app.get("/verticals", tags=["CORE Metadata"])
async def list_verticals():
    """Discover available intelligence verticals and their configurations."""
    return {
        "verticals": [{"id": k, "config": v} for k, v in VERTICAL_PROMPTS.items()],
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

# ============================================================
# RETRY MECHANISM
# ============================================================
async def retry_ollama_request(method: str, url: str, model_name: str, max_retries: int = 2, initial_backoff: float = 0.5, timeout=None, bypass_cb: bool = False, **kwargs):
    """Retries an Ollama API call with exponential backoff + jitter."""
    cb = get_circuit_breaker(model_name)
    last_error = None
    for attempt in range(max_retries + 1):
        if not bypass_cb: # type: ignore
            await cb.check_state()
        
        start_time = time.perf_counter()

        try:
            if ollama_client is None:
                raise RuntimeError("Ollama client not initialized")
            logger.info(f"SYSTEM | {model_name} | API Call: {method} {url} (Attempt {attempt+1})") # type: ignore
            # Log raw request payload (for debugging)
            payload = kwargs.get('json') or kwargs.get('content') or '(empty)'
            logger.info(f"SYSTEM | {model_name} | RAW REQUEST PAYLOAD: {json.dumps(payload) if isinstance(payload, dict) else str(payload)}")

            client_method = getattr(ollama_client, method.lower())
            ollama_resp = await client_method(url, timeout=timeout, **kwargs)

            # Log raw response data for non-streaming calls
            if not getattr(ollama_resp, 'is_stream', False): # Only log full response for non-streaming
                logger.info(f"SYSTEM | {model_name} | RAW RESPONSE DATA (Status {ollama_resp.status_code}): {ollama_resp.text}")

            ollama_resp.raise_for_status()
            if not bypass_cb:
                await cb.record_success() # type: ignore
            
            latency = time.perf_counter() - start_time
            logger.info(f"SYSTEM | {model_name} | Success | Latency: {latency:.2f}s")
            return ollama_resp
        except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadTimeout, httpx.PoolTimeout, httpx.HTTPStatusError) as e:
            if bypass_cb:
                # Don't poison circuit breakers for health checks or meta-calls (Production Fix)
                raise e # type: ignore
                
            last_error = e
            if attempt < max_retries:
                backoff = initial_backoff * (2 ** attempt) * (1 + random.random() * 0.2)
                logger.warning(f"SYSTEM | {model_name} | Retry {attempt+1}/{max_retries+1}: {e}")
                await asyncio.sleep(backoff)

    if not bypass_cb and last_error:
        await cb.record_failure() # type: ignore
    if last_error:
        logger.error(f"SYSTEM | {model_name} | Failed after {max_retries+1} attempts: {last_error}")
        raise last_error
    raise RuntimeError("Unexpected end of retry loop")

@app.get("/", tags=["Frontend"])
async def serve_dashboard():
    """Serve the Arybit AI Node Dashboard."""
    # Use the same frontend_path logic as the static mounts
    frontend_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend")
    index_file = os.path.join(frontend_path, "index.html") # type: ignore
    if os.path.exists(index_file):
        return FileResponse(index_file)
    
    # Fallback to health JSON if dashboard files aren't found
    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "message": "Arybit AI Node Dashboard files not found.",
            "readiness": readiness_state,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    )

@app.get("/ping", tags=["Health"])
async def ping():
    """Ultra-light ping for load balancers."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()} # type: ignore

@app.get("/healthz", tags=["Health"])
async def healthz():
    """Standard Liveness Probe for orchestration tools."""
    return {"status": "ok"}

@app.get("/live", tags=["Health"])
async def live():
    """Lightweight liveness probe."""
    return {"status": "alive"}

@app.get("/health/loadbalancer", tags=["Health"])
async def lb_health():
    """Lightweight endpoint for load balancer health checks."""
    return Response(status_code=200)

@app.get("/debug/pools", tags=["Debug"])
async def debug_pools(user: dict = Depends(enforce_user_access)):
    """Monitor internal connection pool states."""
    if user.get("role") != "system":
        raise HTTPException(status_code=403)
    
    return {
        "ollama_pool": {
            "active_connections": len(ollama_client._transport._pool._connections) if ollama_client else 0,
            "max_connections": ollama_client._transport._pool._max_connections if ollama_client else 0
        } if ollama_client and hasattr(ollama_client, '_transport') else "unavailable",
        "auth_pool": {
            "active_connections": len(auth_client._transport._pool._connections) if auth_client else 0,
            "max_connections": auth_client._transport._pool._max_connections if auth_client else 0
        } if auth_client and hasattr(auth_client, '_transport') else "unavailable",
    }

@app.get("/health/celery", tags=["Health"])
async def health_celery():
    """Check Celery worker connectivity."""
    try:
        i = celery_app.control.inspect()
        active = i.active()
        if active:
            return {"status": "healthy", "workers": len(active)}
        return {"status": "degraded", "message": "No active workers"}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "unhealthy", "error": str(e)})

@app.get("/health/qdrant/pool", tags=["Health"])
async def health_qdrant_pool():
    """Check Qdrant connectivity and collection availability."""
    if not qdrant_client:
        return {"status": "disabled"}
    try:
        # Simple reachability check
        await qdrant_client.get_collections()
        return {"status": "healthy", "collections": len(PLATFORM_COLLECTIONS)}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "unhealthy", "error": str(e)})

@app.get("/status", tags=["Health"])
async def get_system_status():
    """Aggregated system load and readiness status for external monitoring."""
    # Update Qdrant metrics if available
    if qdrant_client and readiness_state["qdrant"]: # type: ignore
        try:
            default_collection = get_vector_collection("core")  # Use standard collection naming
            info = await qdrant_client.get_collection(default_collection)
            qdrant_collection_size.labels(collection=default_collection).set(info.points_count)
        except Exception:
            pass

    async with active_requests_lock: # Protect shared state
        return {
            "status": "ready" if all(readiness_state.values()) else "degraded",
            "active_requests": sum(active_requests_by_type.values()),
            "active_by_model": dict(active_requests_by_model),
            "readiness": readiness_state,
            "uptime_seconds": int(time.time() - getattr(app.state, 'start_time', time.time())),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

@app.get("/ready", tags=["Health"])
async def ready():
    """Detailed readiness probe for infrastructure monitoring."""
    redis_status = "connected" if redis_client is not None else "disabled (memory-only)" # type: ignore

    any_ready = any(readiness_state.values())
    all_ready = all(readiness_state.values())
    
    response = {
        "status": "ready" if all_ready else "degraded" if any_ready else "warming_up",
        "components": readiness_state,
        "redis": redis_status,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    
    if not all_ready: # Return 503 if not fully ready
        return JSONResponse(status_code=503, content=response)
    return response

@app.get("/health/detailed", tags=["Health"])
async def health_detailed_status():
    """Comprehensive health check for orchestration and auto-recovery scripts."""
    return {
        "status": "ready" if all(readiness_state.values()) else "degraded",
        "ollama": { # type: ignore
            "reachable": provider_status["ollama"]["reachable"],
            "models": provider_status["ollama"]["models"],
            "circuit_breakers": {
                model: {"failures": cb.failures, "open": cb.failures >= cb.threshold}
                for model, cb in circuit_breakers.items()
            }
        },
        "redis": {
            "connected": redis_client is not None, # type: ignore
            "circuit_state": "open" if getattr(redis_circuit_breaker, "failures", 0) >= 3 else "closed"
        },
        "qdrant": {
            "status": readiness_state.get("qdrant", False)
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/metrics", tags=["Health"])
async def metrics(user: dict = Depends(enforce_user_access)):
    """Structured metrics for observability - restricted to system services."""
    if user.get("role") != "system":
        raise HTTPException(status_code=403, detail="Metrics access restricted to internal services") # Enforce access # type: ignore
        
    async with active_requests_lock:
        return {
            "readiness": readiness_state,
            "redis": "connected" if redis_client is not None else "disabled (memory-only)",
            "active_requests_by_type": {k.value: v for k, v in active_requests_by_type.items()},
            "active_requests_by_model": dict(active_requests_by_model),
            "circuit_breakers": {
                model: {
                    "failures": cb.failures,
                    "last_failure": cb.last_failure,
                    "threshold": cb.threshold
                } for model, cb in circuit_breakers.items()
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

@app.get("/health", tags=["Health"])
async def health_detailed(request: Request):
    """Detailed health check with Ollama connectivity and vertical awareness"""
    vertical = getattr(request.state, "vertical_id", "core")
 # type: ignore
    if ollama_client is None:
        raise HTTPException(status_code=503, detail="AI Node initializing")
    
    # Chat/inference only needs the LLM path; avoid perpetual "starting" when Qdrant/Redis lag.
    if not readiness_state.get("llm", False):
        return {"status": "starting", "vertical": vertical, "ollama": {"reachable": ollama_client is not None}}

    try:
        # Graceful degradation: Check if vertical-specific collection is ready
        vertical_ready = True
        if qdrant_client and readiness_state["qdrant"]: # type: ignore
            coll = get_vector_collection(vertical)
            try:
                vertical_ready = await qdrant_client.collection_exists(coll)
            except Exception:
                vertical_ready = False

        # Use consistent retry logic for health monitoring
        ollama_resp = await retry_ollama_request("GET", "/api/tags", LLM_MODEL, max_retries=1, bypass_cb=True)
        models = ollama_resp.json().get("models", [])
 # type: ignore
        # Check if any requests are active (FastAPI is busy)
        async with active_requests_lock:
            busy_count = sum(active_requests_by_type.values())

        if busy_count > 0:
            return {
                "status": "busy",
                "vertical": vertical,
                "ollama": { # Indicate busy status
                    "host": OLLAMA_HOST,
                    "reachable": True,
                    "active_tasks": busy_count
                },
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

        return {
            "status": "healthy" if vertical_ready else "degraded",
            "vertical": vertical,
            "vertical_ready": vertical_ready,
            "ollama": {
                "host": OLLAMA_HOST, # Indicate healthy status
                "reachable": True,
                "models_loaded": len(models)
            },
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    except (httpx.HTTPError, httpx.TimeoutException, Exception) as e:
        logger.error(f"Health check failed: {str(e)}")
        return JSONResponse( # Return 503 on failure
            status_code=503,
            content={
                "status": "degraded",
                "vertical": vertical,
                "error": str(e),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        )

@app.get("/health/providers", tags=["Health"])
async def health_providers():
    """Provider-specific health surface for external workers and orchestration."""
    status = await refresh_ollama_provider_status()
    async with active_requests_lock: # Protect shared state # type: ignore
        queue_depth = sum(active_requests_by_type.values())

    payload = {
        "status": "healthy" if status["reachable"] else "degraded",
        "providers": {
            "ollama": {
                "status": "up" if status["reachable"] else "down",
                "models": status["models"],
                "error": status["error"],
                "last_checked": status["last_checked"],
            }
        },
        "queue_depth": queue_depth,
        "readiness": readiness_state,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if not status["reachable"]: # Return 503 if Ollama is not reachable
        return JSONResponse(status_code=503, content=payload)
    return payload

@app.get("/health/redis", tags=["Health"])
async def health_redis():
    """Redis-specific health and connectivity check."""
    if not redis_client:
        return {"status": "disabled", "mode": "memory-only"} # type: ignore
    try:
        start = time.perf_counter()
        await redis_client.ping()
        latency = (time.perf_counter() - start) * 1000
        return {
            "status": "connected",
            "latency_ms": round(latency, 2),
            "mode": "distributed"
        }
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "disconnected", "error": str(e)})

@app.get("/health/redis/pool", tags=["Health"])
async def health_redis_pool():
    """Monitor Redis connection pool health."""
    if not redis_client:
        return {"status": "disabled"} # type: ignore
    try:
        info = await safe_redis_op(redis_client.info("clients"), default={})
        connected = int(info.get("connected_clients", 0)) if info else 0
        return {
            "status": "healthy",
            "connected_clients": connected,
            "max_clients": REDIS_MAX_CONNECTIONS,
            "utilization_pct": round((connected / REDIS_MAX_CONNECTIONS) * 100, 2) if REDIS_MAX_CONNECTIONS else 0,
        }
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "error", "error": str(e)})

def _celery_worker_health_sync():
    ping = celery_app.control.ping(timeout=1.0)
    inspect = celery_app.control.inspect(timeout=1.0)
    active = inspect.active() or {}
    reserved = inspect.reserved() or {}
    return ping, active, reserved

@app.get("/health/workers", tags=["Health"])
async def health_workers():
    """Check Celery worker health and queue depth."""
    try:
        loop = asyncio.get_event_loop() # type: ignore
        ping, active, reserved = await loop.run_in_executor(None, _celery_worker_health_sync)
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "workers": "inspect failed", "error": str(e)},
        )

    if not ping:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "workers": "no responsive workers"},
        )

    queue_depth = sum(len(tasks) for tasks in reserved.values())
    document_queue_depth.set(queue_depth)

    return {
        "status": "healthy",
        "active_workers": len(active),
        "queue_depth": queue_depth,
        "workers": list(active.keys()),
        "document_queue_depth": queue_depth,
    }

@app.get("/grace/metrics", tags=["Usage"])
async def grace_metrics(
    user: dict = Depends(enforce_user_access),
    days: int = Query(7, ge=1, le=30),
):
    """View grace mode usage metrics (admin only)."""
    if user.get("role") != "system": # type: ignore
        raise HTTPException(status_code=403, detail="Admin access required")

    metrics = []
    for i in range(days):
        date = (datetime.now(timezone.utc).date() - timedelta(days=i)).isoformat()
        total = await safe_redis_op(redis_client.get(f"grace:usage:global:{date}"), default=0)
        metrics.append({"date": date, "tokens_used": int(total or 0)})

    return {"metrics": metrics, "days": days}

@app.get("/health/locks", tags=["Health"])
async def health_locks():
    """Test distributed locking capability."""
    if not redis_client:
        return {"status": "disabled", "mode": "memory-only"} # type: ignore

    test_key = f"health:lock_test:{int(time.time())}"
    lock = RedisLock(redis_client, test_key, ttl=5)

    try:
        acquired = await lock.acquire(blocking=False, timeout=2.0)
        if acquired:
            await lock.release()
            return {"status": "healthy", "distributed_locks": "working"}
        return {"status": "degraded", "distributed_locks": "failed_to_acquire"}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(e)})

@app.get("/health/agents", tags=["Health"])
async def health_agents():
    """Check AARAB agent service health and cache status"""
    await aarab_orchestrator.load_agents()
    return { # type: ignore
        "status": "healthy" if aarab_orchestrator.agent_cache else "degraded",
        "agent_count": len(aarab_orchestrator.agent_cache),
        "last_refresh": datetime.fromtimestamp(aarab_orchestrator.last_fetch, timezone.utc).isoformat(),
        "ttl": aarab_orchestrator.cache_ttl
    }

# ============================================================
# MODEL ENDPOINTS
# ============================================================

@app.get("/models", tags=["Models"])
async def list_models(request: Request):
    """List all available models with vertical-specific filtering"""
    vertical = getattr(request.state, "vertical_id", "core")

    if ollama_client is None:
        raise HTTPException(status_code=503, detail="AI Node initializing") # type: ignore

    warmup_complete = readiness_state.get("llm", False) and readiness_state.get("embedding", False)

    try:
        async with active_requests_lock:
            is_busy = sum(active_requests_by_type.values()) > 0

        # Use retry wrapper for stability # type: ignore
        ollama_resp = await retry_ollama_request("GET", "/api/tags", LLM_MODEL, max_retries=1, bypass_cb=True) # Bypass circuit breaker for health
        data = ollama_resp.json()
        
        allowed_for_vertical = get_vertical_models(vertical)

        # Format response
        models = []
        if data.get("models"): # type: ignore
            for model in data["models"]:
                if model.get("name") not in allowed_for_vertical:
                    continue
                models.append({
                    "name": model.get("name"),
                    "model": model.get("model"),
                    "size": model.get("size"),
                    "digest": model.get("digest")
                })

        status = "starting"
        if warmup_complete:
            status = "busy" if is_busy else "idle" # type: ignore
        
        return { # Return model list (include tags during warmup so UI can select a real model)
            "models": models,
            "vertical": vertical,
            "status": status,
            "readiness": readiness_state,
            "count": len(models),
            "llm_default": LLM_MODEL,
            "embedding_default": EMBEDDING_MODEL
        }
    except (httpx.HTTPError, httpx.TimeoutException, Exception) as e:
        logger.error(f"SYSTEM | MODELS | Failed to list models: {str(e)}") # Log errors # type: ignore
        raise HTTPException(status_code=503, detail=f"Ollama unavailable: {str(e)}")

@app.get("/health/gateway")
async def gateway_health():
    return {
        "status": "healthy",
        "verticals": list(VERTICAL_PROMPTS.keys()),
        "ollama_reachable": provider_status["ollama"]["reachable"] # type: ignore
    }

@app.get("/models/{model_name}", tags=["Models"])
async def model_info(model_name: str):
    """Get info about a specific model"""
    validate_model(model_name)
    if ollama_client is None:
        raise HTTPException(status_code=503, detail="AI Node initializing") # type: ignore
    
    try:
        ollama_resp = await retry_ollama_request(
            "POST",
            "/api/show",
            model_name=model_name,
            json={"name": model_name}
        )
        return ollama_resp.json() # Return model info # type: ignore
    except (httpx.HTTPError, httpx.TimeoutException, Exception) as e:
        logger.error(f"SYSTEM | {model_name} | Failed to get model info: {str(e)}")
        raise HTTPException(status_code=503, detail=f"Model not found: {model_name}")

@app.get("/v1/models", tags=["Models"])
async def openai_list_models(request: Request):
    """OpenAI-compatible models list."""
    # Fetch actual models from Ollama to ensure accuracy
    models_resp = await list_models(request) # Reuse internal list_models
    current_time = int(time.time()) # type: ignore
    
    return {
        "object": "list",
        "data": [
            {
                "id": m.get("name"), 
                "object": "model", 
                "created": current_time, 
                "owned_by": "arybit-ai"
            }
            for m in models_resp.get("models", [])
        ]
    }

# ============================================================
# USAGE & GRACE STATUS ENDPOINTS (Frontend-friendly)
# ============================================================

@app.get("/usage", tags=["Usage"])
async def get_usage(
    request: Request,
    user: dict = Depends(enforce_user_access)
):
    """Return current daily token usage and grace/KYC status for the frontend."""
    identity_key = request_identity_key(request, user)
    used = await get_user_usage(identity_key)

    identity = getattr(request.state, "identity", {}) or {} # Get identity from request state # type: ignore
    quota = identity.get("quota_details") or user.get("quota_details") or {}

    grace_max_tokens = int(quota.get("limit") or os.getenv("KYC_GRACE_MAX_TOKENS", "4096"))
    if grace_max_tokens < 100:
        grace_max_tokens = 4096

    kyc_status = user.get("kyc_status", "pending")
    is_grace = kyc_status == "pending"
    remaining = max(0, grace_max_tokens - used)

    return {
        "user_id": user.get("user_id"),
        "kyc_status": kyc_status,
        "is_grace_mode": is_grace,
        "tokens_used_today": used,
        "grace_limit": grace_max_tokens,
        "tokens_remaining": remaining,
        "percent_used": round((used / grace_max_tokens) * 100, 1) if grace_max_tokens > 0 else 0,
        "quota_details": quota,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }


@app.get("/grace-status", tags=["Usage"])
async def grace_status(
    request: Request,
    user: dict = Depends(enforce_user_access)
):
    """Lightweight endpoint for grace banner / UI updates."""
    identity_key = request_identity_key(request, user)
    used = await get_user_usage(identity_key)

    identity = getattr(request.state, "identity", {}) or {} # Get identity from request state # type: ignore
    quota = identity.get("quota_details") or user.get("quota_details") or {}

    grace_max = int(quota.get("limit") or os.getenv("KYC_GRACE_MAX_TOKENS", "4096"))
    if grace_max < 100:
        grace_max = 4096

    remaining = max(0, grace_max - used)
    is_grace_mode = user.get("kyc_status") == "pending"

    return {
        "is_grace_mode": is_grace_mode,
        "tokens_used": used,
        "tokens_remaining": remaining,
        "grace_limit": grace_max,
        "percent_used": round((used / grace_max) * 100, 1) if grace_max > 0 else 0,
        "show_banner": is_grace_mode and remaining < 1500,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

# ============================================================
# CHAT/INFERENCE ENDPOINTS
# ============================================================

@app.post("/chat", tags=["Inference"], response_model=ChatResponse)
async def chat(
    request: Request,
    body: ChatRequest,
    user: dict = Depends(enforce_user_access),
):
    """
    Chat endpoint - send prompt to LLM
    
    Args:
        request: FastAPI request object
        body: ChatRequest Pydantic model
    
    Returns:
        ChatResponse with model response
    """
    if not body.prompt and not body.messages:
        raise HTTPException(status_code=400, detail="Either 'prompt' or 'messages' must be provided")

    vertical = getattr(request.state, "vertical_id", "core")
    request_id = getattr(request.state, "request_id", "unknown") # type: ignore
    
    # Inject Vertical Identity into System Prompt
    v_config = get_vertical_config(vertical)
    if body.messages:
        # Ensure system message matches vertical
        if body.messages[0].role != "system":
            body.messages.insert(0, ChatMessage(role="system", content=v_config["system"]))
        else:
            body.messages[0].content = f"{v_config['system']} {body.messages[0].content}"
    elif body.prompt:
        body.prompt = f"{v_config['system']}\n\nTask: {body.prompt}"

    # Apply grace limits only if unverified
    is_verified = bool(user.get("is_verified")) or user.get("kyc_status") in ["verified", "approved", "verified_institutional"] # type: ignore
    if not is_verified and user.get("role") != "system":
        await enforce_chat_grace_limits(request, body)

    try:
        res = await _perform_chat_logic(request, body, user, request_id)
        business_requests.labels(vertical=vertical, endpoint="chat", status="success").inc()
        return res
    except Exception as e:
        business_requests.labels(vertical=vertical, endpoint="chat", status="error").inc()
        raise e


@app.post("/chat/stream", tags=["Inference"])
async def chat_stream(
    request: Request,
    body: StreamingChatRequest,
    user: dict = Depends(enforce_user_access),
):
    """
    Streaming chat endpoint - returns Server-Sent Events
    
    Args: 
        request: FastAPI request object
        body: StreamingChatRequest Pydantic model
    
    Returns:
        Streaming response with token-by-token output
    """
    base_model = body.model or await router.select_model(body)
    validate_model(base_model)

    # Resolve Vertical Context
    vertical = getattr(request.state, "vertical_id", "core") # type: ignore
    # Inject Vertical Identity into System Prompt
    v_config = get_vertical_config(vertical)
    if body.messages:
        if body.messages[0].role != "system":
            body.messages.insert(0, ChatMessage(role="system", content=v_config["system"]))
        else:
            body.messages[0].content = f"{v_config['system']} {body.messages[0].content}"
    elif body.prompt:
        body.prompt = f"{v_config['system']}\n\nTask: {body.prompt}"
    base_request_id = getattr(request.state, "request_id", "unknown")

    # Apply grace limits only if unverified # type: ignore
    is_verified = bool(user.get("is_verified")) or user.get("kyc_status") in ["verified", "approved", "verified_institutional"]
    if not is_verified and user.get("role") != "system":
        await enforce_stream_grace_limits(request, body)
    
    request_id = f"{base_request_id}-stream"
    logger.info(f"{request_id} | STREAM | Request received | model={base_model or 'auto'} | max_tokens={body.max_tokens}")

    api_key = getattr(request.state, "api_key", "anonymous")
    user_id = user.get("user_id") if isinstance(user, dict) else None
    org_id = int(getattr(request.state, "legal_organization_id", 0))
    identity_key = build_identity_key(user_id, api_key, str(org_id) if org_id else None) # type: ignore

    async def event_generator():
        # Emit immediately so PHP/nginx proxies see bytes before slow context prep / CPU prefill.
        yield f"data: {json.dumps({'type': 'status', 'content': 'Preparing session...', 'step': 'connecting'})}\n\n"
        yield ": connected\n\n"

        try:
            conversation_id, user_content, full_messages = await prepare_chat_context(
                api_key, user_id, body, org_id # type: ignore
            )
        except Exception as prep_err:
            logger.error(f"{request_id} | STREAM | Context preparation failed: {prep_err}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'error': 'Failed to prepare chat context'})}\n\n"
            return

        final_usage = None
        streamed_chars = 0
        assistant_response_content = "" # Track full response for history
        saw_error = False
        async for event in _internal_ollama_stream_generator(
            base_model=base_model,
            full_messages=full_messages,
            body=body,
            request_id=request_id,
            identity_key=identity_key,
            conversation_id=conversation_id,
            user_content=user_content,
            request=request,
            org_id=org_id
        ):
            if event["type"] == "token":
                streamed_chars += len(event.get("token", "")) # Track streamed characters
                assistant_response_content += event.get("token", "")
                yield f"data: {json.dumps({'type': 'token', 'content': event['token'], 'conversation_id': conversation_id})}\n\n"
            elif event["type"] == "citations":
                yield f"data: {json.dumps({'type': 'citations', 'items': event['items']})}\n\n"
            elif event["type"] == "risk":
                yield f"data: {json.dumps({'type': 'risk', 'items': event['items']})}\n\n"
            elif event["type"] == "done":
                final_usage = event["usage"]
                await update_conversation_history(identity_key, conversation_id, user_content, assistant_response_content, org_id) # Final update
                logger.info(
                        f"{request_id} | STREAM | Usage event emitted | " # Log final usage # type: ignore
                    f"tokens_used={final_usage.get('tokens_used')} | "
                    f"total_used={final_usage.get('total_used')} | "
                    f"remaining={final_usage.get('remaining')}"
                )
                yield f"data: {json.dumps({'usage': final_usage})}\n\n"
                yield "data: [DONE]\n\n"
                return
            elif event["type"] == "status":
                yield f"data: {json.dumps({**event, 'conversation_id': conversation_id})}\n\n"
            elif event["type"] == "keep_alive":
                # Yield a comment to keep the connection open
                # This is standard SSE practice (Production Fix)
                yield ": keep-alive\n\n"
            elif event["type"] == "error":
                saw_error = True
                await update_conversation_history(identity_key, conversation_id, user_content, assistant_response_content, org_id) # Update history even on error
                yield f"data: {json.dumps({'error': event['error']})}\n\n" # type: ignore

        if streamed_chars == 0 and not saw_error and final_usage is None:
            logger.warning(f"{request_id} | STREAM | Completed with zero tokens")
            yield f"data: {json.dumps({'error': 'No response generated before the stream ended. On CPU this usually means prefill timed out — retry with phi3:mini or a shorter prompt.'})}\n\n"
            yield "data: [DONE]\n\n"
        elif final_usage is None and streamed_chars > 0 and not saw_error: # Fallback usage if Ollama doesn't send done payload
            fallback_prompt_tokens = estimate_tokens(user_content)
            fallback_eval_tokens = estimate_tokens("x" * streamed_chars)
            await record_actual_usage(
                identity_key=identity_key,
                model=base_model,
                prompt_eval_count=fallback_prompt_tokens,
                eval_count=fallback_eval_tokens,
                request_type="chat_stream_fallback",
                request=request,
            )
            await update_conversation_history(identity_key, conversation_id, user_content, assistant_response_content, org_id) # Final update

            user_quota = getattr(request.state, "identity", {}).get("quota_details") or user.get("quota_details") or {}
            limit = int(user_quota.get("limit") or os.getenv("KYC_GRACE_MAX_TOKENS", "4096"))
            if limit < 100:
                limit = 4096

            fallback_usage = {
                "prompt_eval_count": fallback_prompt_tokens,
                "eval_count": fallback_eval_tokens,
                "total_duration": None,
                "tokens_used": getattr(request.state, "tokens_used_this_request", 0),
                "total_used": getattr(request.state, "total_usage_after_request", 0),
                "remaining": max(0, limit - getattr(request.state, "total_usage_after_request", 0)),
                "limit": limit,
                "estimated": True,
            }
            logger.warning(
                f"{request_id} | STREAM | Ollama done payload missing; emitted fallback usage | "
                f"tokens_used={fallback_usage['tokens_used']} | total_used={fallback_usage['total_used']}"
            )
            yield f"data: {json.dumps({'usage': fallback_usage})}\n\n"
            yield "data: [DONE]\n\n"
    
    response = StreamingResponse(
        event_generator(),
        media_type="text/event-stream; charset=utf-8",
        headers={
            "X-Request-ID": request_id,
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
        }
    )

    identity_key = request_identity_key(request, user)
    initial_total_used = await get_user_usage(identity_key) # Get initial usage for headers

    user_quota = getattr(request.state, "identity", {}).get("quota_details") or user.get("quota_details") or {}
    LIMIT = int(user_quota.get("limit") or os.getenv("KYC_GRACE_MAX_TOKENS", "4096"))
    if LIMIT < 100: LIMIT = 4096

    response.headers["X-Tokens-Used"] = str(initial_total_used)
    response.headers["X-Tokens-Remaining"] = str(max(0, LIMIT - initial_total_used))
    response.headers["X-KYC-Limit"] = str(LIMIT)

    return response

# ============================================================
# EMBEDDING ENDPOINTS
# ============================================================

@app.post("/embeddings", tags=["Embeddings"])
async def embeddings(
    request: Request,
    body: EmbeddingRequest,
    user: dict = Depends(enforce_user_access),
):
    """
    Generate embeddings for text
    
    Args:
        request: FastAPI request object
        body: EmbeddingRequest Pydantic model
    
    Returns:
        Embedding vector
    """
    if ollama_client is None:
        raise HTTPException(status_code=503, detail="AI Node initializing")

    request_id = getattr(request.state, "request_id", "unknown") # Get request_id
    model = body.model or EMBEDDING_MODEL # Use body for model selection
    validate_model(model)
    start_time = time.perf_counter()
 # type: ignore
    # Apply grace limits only if unverified
    is_verified = bool(user.get("is_verified")) or user.get("kyc_status") in ["verified", "approved", "verified_institutional"]
    if not is_verified and user.get("role") != "system":
        await enforce_embeddings_grace_limits(request, body)

    async with get_model_semaphore(model), RequestTracker(RequestType.EMBEDDINGS, model):
            
        try: # type: ignore
            log_ctx(request_id, "Embedding request initiated", model=model)
            
            ollama_resp = await retry_ollama_request(
                "POST",
                "/api/embeddings",
                model_name=model,
                json={"model": model, "prompt": body.text},
                timeout=REQUEST_TIMEOUT)
            data = ollama_resp.json()

            duration = time.perf_counter() - start_time
            logger.info(f"{request_id} | {model} | Embedding generated in {duration:.2f}s")

            # Log AI Usage (1 per vector for embeddings) # type: ignore
            asyncio.create_task(log_ai_usage(user_id=request_identity_key(request, user), model=model, tokens_used=1, request_type="embeddings", request_id=request_id))

            return {
                "model": model,
                "embedding": data.get("embedding"),
                "dimension": len(data.get("embedding", []))
            }
        
        except asyncio.TimeoutError: # type: ignore
            logger.error(f"{request_id} | {model} | Embedding request timed out")
            raise HTTPException(status_code=504, detail="Embedding generation timed out")
        except asyncio.CancelledError:
            logger.warning(f"{request_id} | {model} | Client disconnected during embedding")
            raise
        except Exception as e:
            logger.error(f"{request_id} | {model} | Embedding error after retries: {str(e)}")
            raise HTTPException(status_code=503, detail=str(e))

# ============================================================
# GENERATE ENDPOINT (Legacy Ollama API)
# ============================================================

@app.post("/generate", tags=["Inference"])
async def generate(
    request: Request,
    body: ChatRequest,
    user: dict = Depends(enforce_user_access),
):
    """
    Generate endpoint for text generation (legacy Ollama API)
    
    Args:
        request: FastAPI request object
        body: ChatRequest Pydantic model
    
    Returns:
        Generated text
    """
    if ollama_client is None:
        raise HTTPException(status_code=503, detail="AI Node initializing")

    if not body.prompt: # type: ignore
        raise HTTPException(status_code=400, detail="The 'prompt' field is required for the generate endpoint")

    # Resolve Vertical Context
    vertical = getattr(request.state, "vertical_id", "core")
    # Inject Vertical Identity into System Prompt
    v_config = get_vertical_config(vertical)
    if body.messages: # This branch is unlikely for /generate but for consistency
        if body.messages[0].role != "system":
            body.messages.insert(0, ChatMessage(role="system", content=v_config["system"]))
        else:
            body.messages[0].content = f"{v_config['system']} {body.messages[0].content}"
    elif body.prompt:
        body.prompt = f"{v_config['system']}\n\nTask: {body.prompt}"

    model = body.model or await router.select_model(body) # Select model
    validate_model(model)
    request_id = getattr(request.state, "request_id", "unknown") # type: ignore
    start_time = time.perf_counter()
    
    # Standardize conversation tracking for generate
    api_key = getattr(request.state, "api_key", "anonymous")
    user_id = user.get("user_id") if isinstance(user, dict) else None
    org_id = int(getattr(request.state, "legal_organization_id", 0))
    conversation_id, user_content, _ = await prepare_chat_context(api_key, user_id, body, org_id)
    identity_key = build_identity_key(user_id, api_key, str(org_id) if org_id else None)

    # Apply grace limits only if unverified
    is_verified = bool(user.get("is_verified")) or user.get("kyc_status") in ["verified", "approved", "verified_institutional"]
    if not is_verified and user.get("role") != "system": # type: ignore
        await enforce_chat_grace_limits(request, body)

    async with get_model_semaphore(model), RequestTracker(RequestType.GENERATE, model):
            
        try:
            logger.info(f"{request_id} | {model} | 🔒 Generate lock acquired")

            ollama_resp = await retry_ollama_request(
                "POST",
                "/api/generate",
                model_name=model,
                json={
                    "model": model,
                    "prompt": body.prompt,
                    "stream": False,
                    "keep_alive": "5m",
                    "options": {
                        "temperature": body.temperature, # type: ignore
                        "num_predict": body.max_tokens,
                        "num_ctx": DEFAULT_NUM_CTX
                    },
                },
                timeout=REQUEST_TIMEOUT
            )

            data = ollama_resp.json()
            duration = time.perf_counter() - start_time # type: ignore
            
            # High-Value A: Consistent memory for legacy generate endpoint
            assistant_response = data.get("response", "")
            await update_conversation_history(identity_key, conversation_id, user_content, assistant_response, org_id)
            
            await record_actual_usage( # Record usage
                identity_key=identity_key,
                model=model,
                prompt_eval_count=data.get("prompt_eval_count"),
                eval_count=data.get("eval_count"),
                request_type="generate",
                request=request,
            )
            
            logger.info(f"[{request_id}] Generation completed in {duration:.2f}s")
            
            return ChatResponse(
                model=model,
                response=data.get("response", ""),
                done=data.get("done", True),
                conversation_id=conversation_id,
                context=data.get("context"),
                total_duration=data.get("total_duration"),
                prompt_eval_count=data.get("prompt_eval_count"),
                eval_count=data.get("eval_count")
            )
        
        except asyncio.TimeoutError: # type: ignore
            logger.error(f"{request_id} | {model} | Generate request timed out at endpoint level")
            raise HTTPException(status_code=504, detail="Generation timed out")
        except asyncio.CancelledError:
            logger.warning(f"{request_id} | {model} | Client disconnected during generate")
            raise
        except Exception as e:
            logger.error(f"{request_id} | {model} | Generate error: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))
        finally:
            logger.info(f"[{request_id}] 🔓 Generate processing completed for {model}")

# ============================================================
# OPENAI COMPATIBILITY ENDPOINT
# ============================================================

@app.post("/v1/chat/completions", tags=["Inference"])
async def openai_chat_completions(
    request: Request,
    body: OpenAIChatRequest,
    user: dict = Depends(enforce_user_access),
):
    """
    OpenAI-compatible chat completions endpoint.
    Supports both standard and streaming requests.
    """
    # Map OpenAI model name to our internal allowed models
    # Apply grace limits only if unverified
    is_verified = bool(user.get("is_verified")) or user.get("kyc_status") in ["verified", "approved", "verified_institutional"] # type: ignore
    if not is_verified and user.get("role") != "system":
        await enforce_openai_grace_limits(request, body)

    # If the requested model is 'gpt-3.5-turbo' or similar, we default to our main LLM (Production Fix)
    base_model = body.model if body.model in ALLOWED_MODELS else LLM_MODEL
    
    # Create an internal ChatRequest from the OpenAI request
    internal_body = ChatRequest(
        messages=body.messages,
        model=base_model,
        temperature=body.temperature,
        max_tokens=body.max_tokens
    )
    
    request_id = getattr(request.state, "request_id", f"oa-{uuid.uuid4().hex[:8]}")
    created_time = int(time.time())

    if body.stream:
        async def openai_event_generator():
            stream_request = StreamingChatRequest(**internal_body.model_dump(), stream=True)
            api_key = getattr(request.state, "api_key", "anonymous")
            user_id = user.get("user_id") if isinstance(user, dict) else None # Get user_id # type: ignore
            org_id = int(getattr(request.state, "legal_organization_id", 0))
            conversation_id, user_content, full_messages = await prepare_chat_context(api_key, user_id, stream_request, org_id)
            identity_key = build_identity_key(user_id, api_key, str(org_id) if org_id else None)

            async for event in _internal_ollama_stream_generator(
                base_model=base_model,
                full_messages=full_messages,
                body=stream_request,
                request_id=request_id,
                identity_key=identity_key,
                conversation_id=conversation_id,
                user_content=user_content,
                request=request, # type: ignore
                org_id=org_id
            ):
                if event.get("type") == "token":
                    chunk = {
                        "id": f"chatcmpl-{request_id}",
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": base_model,
                        "choices": [{"index": 0, "delta": {"content": event["token"]}, "finish_reason": None}]
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"
                elif event.get("type") == "done":
                    final_chunk = {
                        "id": f"chatcmpl-{request_id}",
                        "object": "chat.completion.chunk",
                        "created": created_time,
                        "model": base_model,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
                    }
                    yield f"data: {json.dumps(final_chunk)}\n\n"
                    yield "data: [DONE]\n\n"
                elif event.get("type") == "keep_alive":
                    yield ": keep-alive\n\n"

        return StreamingResponse(openai_event_generator(), media_type="text/event-stream")

    else:
        # Non-streaming OpenAI response
        # FIX: Call internal logic directly to avoid double auth dependencies
        response_data = await _perform_chat_logic(request, internal_body, user, request_id)

        # Convert our ChatResponse to OpenAIChatResponse
        prompt_tokens = response_data.prompt_eval_count or 0
        completion_tokens = response_data.eval_count or 0

        return OpenAIChatResponse(
            id=f"chatcmpl-{request_id}",
            created=created_time,
            model=response_data.model,
            choices=[
                OpenAIChoice(
                    index=0,
                    message=ChatMessage(role="assistant", content=response_data.response),
                    finish_reason="stop"
                )
            ],
            usage=OpenAIUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens
            )
        )

# ============================================================
# CHAT HISTORY ENDPOINTS
# ============================================================

@app.get("/chat/history", tags=["Chat History"])
async def list_chat_history(
    request: Request,
    user: dict = Depends(enforce_user_access),
    limit: int = Query(50, ge=1, le=200, description="Max number of conversations to return"),
    offset: int = Query(0, ge=0, description="Pagination offset")
):
    """
    List conversation summaries for the authenticated user with pagination and totals.
    Returns summaries (IDs, titles, counts, timestamps) along with pagination metadata.
    """
    # Tenant Isolation: Ensure history is scoped to the organization
    org_id = getattr(request.state, "legal_organization_id", "0")
    legal_organization_id = int(org_id) if str(org_id).isdigit() else 0 # Convert to int # type: ignore

    identity_key = request_identity_key(request, user)
    results = await list_conversations_redis(identity_key, legal_organization_id) # Filter by organization
    
    if not results:
        async with conversation_lock:
            for (ikey, cid), data in list(conversation_store.items()):
                if ikey != identity_key or data.get("org_id", 0) != legal_organization_id:
                    continue

                title = "New conversation" # type: ignore
                messages_deque = data.get("messages", deque())
                if messages_deque:
                    for msg in messages_deque:
                        if msg.get("role") != "user":
                            continue
                        raw = msg.get("content")
                        if raw is None:
                            continue
                        content = str(raw).strip()
                        if not content:
                            continue
                        title = (content[:55] + "...") if len(content) > 55 else content
                        break

                results.append({
                    "conversation_id": cid,
                    "title": title,
                    "last_seen": datetime.fromtimestamp(
                        data.get("last_seen", time.time()), timezone.utc
                    ).isoformat(),
                    "message_count": len(messages_deque),
                    "org_id": data.get("org_id", 0)
                })

    results.sort(key=lambda x: x["last_seen"], reverse=True)
    total = len(results)

    paginated = results[offset : offset + limit]

    return {
        "conversations": paginated,
        "total": total,
        "returned": len(paginated),
        "limit": limit,
        "offset": offset
    }


@app.get("/chat/history/{conversation_id}", tags=["Chat History"])
async def get_chat_history(
    conversation_id: str,
    request: Request,
    user: dict = Depends(enforce_user_access),
    max_messages: int = Query(100, ge=1, le=500)
):
    org_id = getattr(request.state, "legal_organization_id", "0") # type: ignore
    legal_organization_id = int(org_id) if str(org_id).isdigit() else 0 # Convert to int

    identity_key = request_identity_key(request, user)

    # Tenant Isolation: Verify the conversation belongs to this organization
    loaded = await load_conversation(identity_key, conversation_id, legal_organization_id)
    if not loaded:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = list(loaded.get("messages", []))  # safe list conversion
    total_available = len(messages)

    if total_available > max_messages:
        messages = messages[-max_messages:]

    return {
        "conversation_id": conversation_id,
        "messages": messages,
        "message_count": len(messages),
        "total_available": total_available,
        "last_seen": datetime.fromtimestamp(
            loaded["last_seen"], timezone.utc
        ).isoformat()
    }


@app.delete("/chat/history/{conversation_id}", tags=["Chat History"])
async def delete_chat_history(
    conversation_id: str,
    request: Request,
    user: dict = Depends(enforce_user_access)
):
    """Delete a specific conversation."""
    identity_key = request_identity_key(request, user)
    
    org_id = getattr(request.state, "legal_organization_id", "0") # type: ignore
    legal_organization_id = int(org_id) if str(org_id).isdigit() else 0 # Convert to int

    await delete_conversation(identity_key, conversation_id, legal_organization_id)
    return {"status": "deleted", "conversation_id": conversation_id}

def _sanitize_workstream_title(raw: str, max_len: int = 100) -> str:
    """Normalize LLM-generated titles for storage (first line, no markdown, capped length)."""
    t = (raw or "").strip().split("\n")[0].strip().replace("**", "")
    if len(t) > max_len:
        t = t[: max_len - 3].rstrip() + "..."
    return t


@app.post("/chat/history/rename", tags=["Chat History"])
async def rename_chat_history(
    request: Request,
    conversation_id: str = Body(..., embed=True),
    title: str = Body(..., embed=True, max_length=500),
    user: dict = Depends(enforce_user_access)
):
    """Explicitly set a human-friendly title for a workstream."""
    title = _sanitize_workstream_title(title)
    if not title:
        raise HTTPException(status_code=400, detail="Title cannot be empty")
    identity_key = request_identity_key(request, user)
    org_id = getattr(request.state, "legal_organization_id", "0") # type: ignore
    
    key = (identity_key, conversation_id)
    async with conversation_lock:
        if key in conversation_store:
            conversation_store[key]["title"] = title
            
    if redis_client:
        rkey = f"conv:{identity_key}:{conversation_id}"
        await safe_redis_op(redis_client.hset(rkey, "title", title))
        
    return {"status": "updated", "conversation_id": conversation_id, "title": title}

async def register_legal_document(
    task_id: int, 
    org_id: int, 
    filename: str, 
    mime: str, 
    size: int, 
    hash_sum: str, 
    request_id: str = "unknown"
) -> int:
    """Register document via internal auth service with strong safety."""
    if org_id <= 0: # type: ignore
        logger.warning(f"{request_id} | DB | org_id was {org_id} → forced to 1")
        org_id = 1

    if not auth_internal_client:
        logger.warning(f"{request_id} | DB | No internal client available")
        return random.randint(100000, 999999)

    try:
        resp = await auth_internal_client.post( # type: ignore
            "/ai-node/core/internal/register-document",
            json={
                "organization_id": org_id,
                "task_id": task_id,
                "filename": filename,
                "mime_type": mime,
                "file_size": size,
                "hash_sha256": hash_sum,
                "request_id": request_id
            },
            headers={
                "X-Internal-Secret": INTERNAL_SERVICE_SECRET,
                "X-Internal-Service": INTERNAL_SERVICE_NAME,
                "X-Request-ID": request_id,
                "X-Organization-ID": str(org_id)
            },
            timeout=httpx.Timeout(12.0)
        )

        if resp.status_code == 404:
            logger.error(f"{request_id} | DB | Registration endpoint 404 - using fallback")
            return random.randint(100000, 999999)
 # type: ignore
        if resp.status_code >= 400:
            logger.warning(f"{request_id} | DB | Registration failed {resp.status_code}: {resp.text[:250]}")
            return random.randint(100000, 999999)

        data = resp.json()
        doc_id = data.get("doc_id") or data.get("id") or random.randint(100000, 999999)
        
        logger.info(f"{request_id} | DB | ✅ Document registered → doc_id={doc_id} | org={org_id}") # type: ignore
        return doc_id

    except Exception as e:
        logger.error(f"{request_id} | DB | Registration exception: {e}", exc_info=True)
        return random.randint(100000, 999999)

class AetherisVisualizationGenerator:
    """Aetheris Agent - Multimodal Cinematic Output"""
    
    @staticmethod
    async def generate_infographic(data: dict, request_id: str) -> dict:
        """Generate an infographic from data using the LLM for structure."""
        prompt = f"""
        Create a beautiful, modern HTML/CSS infographic based on this data:
        {json.dumps(data, indent=2)} # type: ignore
        
        Requirements:
        - Use Tailwind CSS or modern inline styles
        - Include charts using simple CSS or emoji data bars
        - Make it responsive and visually stunning
        - Add cinematic gradients and glassmorphism effects
        - Return ONLY the HTML/CSS code snippet
        """
        
        # Reusing internal chat logic to generate the code
        resp = await retry_ollama_request(
            "POST", "/api/chat", LLM_MODEL,
            json={
                "model": LLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_predict": 4096}
            }
        )
        viz_code = resp.json().get("message", {}).get("content", "")
        
        return {
            "type": "web_view",
            "content_type": "aetheris",
            "content": {
                "description": data.get("title", "AI-Generated Infographic"),
                "visualization": viz_code,
                "data": data
            },
            "options": {"visualization_type": "infographic"}
        }
    
    @staticmethod
    async def generate_video_script(topic: str, duration: int = 60) -> dict:
        """Generate a structured video script metadata."""
        # Implementation omitted for brevity, returning placeholder
        return {
            "type": "web_view",
            "content_type": "aetheris",
            "content": {
                "scenes": [{"description": f"Scene 1: Introduction to {topic}", "narration": "In a world..."}],
                "narration": f"This is a cinematic overview of {topic}."
            },
            "options": {"visualization_type": "video_script"}
        }


# ============================================================
# ENHANCED AARAB COMPONENTS - Add after existing imports
# ============================================================

class DistributedAgentMetrics:
    """Redis-backed agent metrics for multi-node consistency"""
    
    def __init__(self, redis_client=None):
        self.redis = redis_client
        self.local_cache = defaultdict(lambda: {
            'tasks_processed': 0,
            'total_response_time': 0.0,
            'success_count': 0,
            'failure_count': 0
        })
    
    async def record_metric(self, agent_type: str, duration_ms: float, success: bool = True, org_id: int = 0):
        """Record agent performance metric"""
        self.local_cache[agent_type]['tasks_processed'] += 1
        self.local_cache[agent_type]['total_response_time'] += duration_ms
        if success:
            self.local_cache[agent_type]['success_count'] += 1
        else:
            self.local_cache[agent_type]['failure_count'] += 1
        
        # If Redis available, sync asynchronously
        if self.redis:
            try:
                key = f"agent_metrics:{agent_type}"
                await self.redis.hincrby(key, 'tasks_processed', 1)
                await self.redis.hincrbyfloat(key, 'total_response_time', duration_ms)
                if success:
                    await self.redis.hincrby(key, 'success_count', 1)
                else:
                    await self.redis.hincrby(key, 'failure_count', 1)
                await self.redis.expire(key, 86400)
            except Exception as e:
                logger.warning(f"Redis metric sync failed: {e}")
    
    async def get_agent_metrics(self, agent_type: str) -> dict:
        """Get metrics from local cache"""
        cache = self.local_cache.get(agent_type, {})
        tasks = cache.get('tasks_processed', 0)
        total_time = cache.get('total_response_time', 0.0)
        successes = cache.get('success_count', 0)
        
        return {
            'tasks_processed': tasks,
            'avg_response_time_ms': round(total_time / max(tasks, 1), 2),
            'success_rate': round(successes / max(tasks, 1), 3) if tasks > 0 else 1.0,
            'failure_count': cache.get('failure_count', 0),
            'source': 'local'
        }
    
    async def get_hourly_trends(self, agent_type: str, hours: int = 24) -> list:
        """Get hourly trends (placeholder for Redis implementation)"""
        return []
    
    async def get_organizational_metrics(self, org_id: int) -> dict:
        """Get organizational metrics (placeholder)"""
        return {}


class DocumentFingerprinter:
    """Advanced document analysis with weighted keyword scoring"""
    
    KEYWORD_CATEGORIES = {
        'legal': {
            'keywords': ['law', 'legal', 'contract', 'agreement', 'terms', 'compliance', 
                        'regulation', 'statute', 'clause', 'section', 'article'],
            'weight': 1.5,
            'density_threshold': 0.02
        },
        'technical': {
            'keywords': ['algorithm', 'code', 'function', 'system', 'architecture', 
                        'technical', 'specification', 'api', 'database', 'protocol'],
            'weight': 1.2,
            'density_threshold': 0.015
        },
        'research': {
            'keywords': ['research', 'study', 'analysis', 'methodology', 'finding', 
                        'conclusion', 'hypothesis', 'experiment', 'data', 'result'],
            'weight': 1.1,
            'density_threshold': 0.012
        }
    }
    
    @staticmethod
    def analyze_document_enhanced(content: str, filename: str) -> dict:
        """Enhanced document analysis with weighted scoring"""
        content_lower = content.lower()
        word_count = len(content.split()) or 1
        
        # Calculate weighted scores
        category_scores = {}
        for category_name, category_config in DocumentFingerprinter.KEYWORD_CATEGORIES.items():
            occurrences = sum(content_lower.count(kw) for kw in category_config['keywords'])
            density = occurrences / word_count
            score = density * category_config['weight']
            if density >= category_config['density_threshold']:
                score *= 1.2
            category_scores[category_name] = min(score, 1.0)
        
        # Determine primary type
        sorted_categories = sorted(category_scores.items(), key=lambda x: x[1], reverse=True)
        primary_type = sorted_categories[0][0] if sorted_categories else "general"
        
        return {
            "primary_type": primary_type,
            "category_scores": category_scores,
            "word_count": word_count,
            "has_citations": bool(re.search(r"\(?\d{4}\)?", content)),
            "is_long_document": word_count > 5000,
            "confidence_threshold": max(0.3, min(0.95, sorted_categories[0][1] if sorted_categories else 0.3))
        }


class EnhancedAgentScorer:
    """Enhanced agent scoring with confidence calibration"""
    
    @staticmethod
    def calculate_relevance_score(doc_analysis: dict, agent_config: dict) -> float:
        """Calculate relevance score between document and agent"""
        agent_type = agent_config.get('agent_type', '')
        primary_type = doc_analysis.get("primary_type", "general")
        
        # Simple scoring for now - can be expanded
        optimal_mapping = {
            'legal': ['truth_seeker', 'precision_analyst'],
            'technical': ['reasoner', 'universal_generalist'],
            'research': ['trend_predictor', 'breakthrough_monitor', 'multimodal_synthesizer'],
            'general': ['universal_generalist', 'summarizer'],
        }
        
        score = 0.3  # Base score
        if agent_type in optimal_mapping.get(primary_type, []):
            score += 0.4
        
        if doc_analysis.get('is_long_document', False) and agent_type in ('summarizer', 'multimodal_synthesizer'):
            score += 0.2
        
        if doc_analysis.get('has_citations', False) and agent_type == 'truth_seeker':
            score += 0.1
        
        return min(score, 1.0)


# Initialize enhanced components
distributed_metrics = DistributedAgentMetrics()
document_fingerprinter = DocumentFingerprinter()
enhanced_agent_scorer = EnhancedAgentScorer()

class AARABAgentOrchestrator:
    """Orchestrates document processing across AARAB v4.0 agents via PHP Gateway"""

    AGENT_CAPABILITIES = {
        'veritas': {
            'agent_type': 'truth_seeker',
            'name': 'Veritas',
            'specialties': ['validation', 'source_verification', 'provenance', 'bias_detection'],
            'optimal_for': ['legal_validation', 'fact_checking', 'source_traceability'],
        },
        'eclaro': {
            'agent_type': 'reasoner',
            'name': 'Eclaro',
            'specialties': ['causal_analysis', 'technical_reasoning', 'breakthrough_analysis'],
            'optimal_for': ['technical_documents', 'scientific_papers', 'complex_analysis'],
        },
        'incepta': {
            'agent_type': 'ideator',
            'name': 'Incepta',
            'specialties': ['hypothesis_generation', 'innovation_sparking', 'data_synthesis'],
            'optimal_for': ['brainstorming', 'research_design', 'creative_problem_solving'],
        },
        'clarion': {
            'agent_type': 'summarizer',
            'name': 'Clarion',
            'specialties': ['executive_summarization', 'impact_briefing', 'concise_communication'],
            'optimal_for': ['document_summaries', 'executive_briefs', 'report_synthesis'],
        },
        'aetheris': {
            'agent_type': 'multimodal_visualizer',
            'name': 'Aetheris',
            'specialties': ['video_synthesis', 'infographic_design', 'visual_storytelling'],
            'optimal_for': ['visual_content', 'presentations', 'infographics'],
        },
        'chronos': {
            'agent_type': 'temporal_forecaster',
            'name': 'Chronos',
            'specialties': ['trend_forecasting', 'timeline_analysis', 'roadmap_generation'],
            'optimal_for': ['market_analysis', 'trend_prediction', 'strategic_planning'],
        },
        'lumina': {
            'agent_type': 'viz_architect',
            'name': 'Lumina',
            'specialties': ['dashboard_design', 'interactive_storytelling', 'insight_delivery'],
            'optimal_for': ['data_visualization', 'dashboards', 'analytics_reports'],
        },
        'novara': {
            'agent_type': 'trend_predictor',
            'name': 'Novara',
            'specialties': ['trend_prediction', 'breakthrough_scanning', 'opportunity_detection'],
            'optimal_for': ['emerging_technologies', 'market_opportunities', 'innovation_scouting'],
        },
        'vanguarda': {
            'agent_type': 'breakthrough_monitor',
            'name': 'Vanguarda',
            'specialties': ['research_monitoring', 'knowledge_synthesis', 'global_alerts'],
            'optimal_for': ['competitive_intelligence', 'patent_monitoring', 'research_tracking'],
        },
        'equinox': {
            'agent_type': 'multimodal_synthesizer',
            'name': 'Equinox',
            'specialties': ['holistic_synthesis', 'multimodal_reporting', 'neutral_analysis'],
            'optimal_for': ['balanced_assessments', 'controversial_topics', 'comprehensive_reports'],
        },
        'voxis': {
            'agent_type': 'voice_narrator',
            'name': 'Voxis',
            'specialties': ['voice_synthesis', 'audio_narration', 'spoken_explanation'],
            'optimal_for': ['audio_content', 'podcasts', 'voice_overs'],
        },
        'eximio': {
            'agent_type': 'precision_analyst',
            'name': 'Eximio',
            'specialties': ['patent_search', 'prior_art_analysis', 'precision_research'],
            'optimal_for': ['patent_analysis', 'legal_research', 'medical_literature'],
        },
        'omnis': {
            'agent_type': 'universal_generalist',
            'name': 'Omnis',
            'specialties': ['cross_domain_intelligence', 'versatile_reasoning', 'general_research'],
            'optimal_for': ['general_documents', 'multi_domain_topics', 'default_routing'],
        },
        'lumenix': {
            'agent_type': 'empathy_engine',
            'name': 'Lumenix',
            'specialties': ['emotional_intelligence', 'tone_tuning', 'stakeholder_management'],
            'optimal_for': ['customer_feedback', 'stakeholder_communication', 'sensitive_topics'],
        },
        'solara': {
            'agent_type': 'strategic_foresight',
            'name': 'Solara',
            'specialties': ['scenario_modelling', 'strategic_foresight', 'long_term_planning'],
            'optimal_for': ['strategic_planning', 'scenario_analysis', 'future_outlook'],
        },
        'vespera': {
            'agent_type': 'daily_brief_expert',
            'name': 'Vespera',
            'specialties': ['daily_synthesis', 'executive_briefing', 'wrap_up_reporting'],
            'optimal_for': ['daily_reports', 'news_summaries', 'end_of_day_briefs'],
        },
        'sentia': {
            'agent_type': 'sentiment_analyst',
            'name': 'Sentia',
            'specialties': ['sentiment_analysis', 'context_understanding', 'crisis_monitoring'],
            'optimal_for': ['social_media', 'brand_monitoring', 'customer_sentiment'],
        },
        'spectra': {
            'agent_type': 'multidimension_analyst',
            'name': 'Spectra',
            'specialties': ['multi_perspective_analysis', 'balanced_synthesis', 'complex_reasoning'],
            'optimal_for': ['complex_controversial_topics', 'policy_analysis', 'ethical_dilemmas'],
        },
        'vigilis': {
            'agent_type': 'realtime_monitor',
            'name': 'Vigilis',
            'specialties': ['real_time_monitoring', 'event_tracking', 'streaming_intelligence'],
            'optimal_for': ['live_data', 'news_monitoring', 'streaming_analytics'],
        },
        'astraeon': {
            'agent_type': 'personalized_ai',
            'name': 'Astraeon',
            'specialties': ['personalized_research', 'profile_optimization', 'hyper_relevance'],
            'optimal_for': ['user_specific_content', 'personalized_recommendations', 'adaptive_learning'],
        },
    }

    def __init__(self):
        self.agent_cache = {}  # agent_type -> config
        self.capability_aliases = {}  # capability_key -> agent_type
        self.agent_metrics = defaultdict(lambda: {'tasks_processed': 0, 'avg_response_time': 0.0, 'success_rate': 1.0})
        self.last_fetch = 0
        self.cache_ttl = 300  # 5 minutes cache TTL
        self._lock = asyncio.Lock()
        
        # Enhanced Components
        self.metrics = distributed_metrics
        self.fingerprinter = document_fingerprinter
        self.scorer = enhanced_agent_scorer

    async def route_document_to_agent_enhanced(
        self, content: str, filename: str, content_type: str, prompt: Optional[str] = None
    ) -> dict:
        """Enhanced routing with weighted scoring and confidence calibration"""
        doc_analysis = self.fingerprinter.analyze_document_enhanced(content, filename)
        agent_scores = {atype: self.scorer.calculate_relevance_score(doc_analysis, cfg)
                        for atype, cfg in self.agent_cache.items()}
        
        if not any(s > 0.1 for s in agent_scores.values()):
            best_agent = "universal_generalist"
            confidence = 0.3
        else:
            best_agent = max(agent_scores, key=agent_scores.get)
            confidence = agent_scores[best_agent]
            
        routing_reason = self._generate_routing_explanation(best_agent, doc_analysis, confidence)
        logger.info(f"AARAB | Routed to {best_agent} | confidence: {confidence:.2f} | Reason: {routing_reason}")
        
        return {
            "selected_agent": best_agent,
            "agent_config": self.agent_cache[best_agent],
            "confidence": round(confidence, 3),
            "document_analysis": doc_analysis,
            "routing_reason": routing_reason
        }
    
    def _generate_routing_explanation(self, agent_type: str, analysis: dict, confidence: float) -> str:
        reasons = [f"Detected {analysis['primary_type']} context"]
        if analysis.get('has_citations'): reasons.append("contains citations")
        if analysis.get('word_count', 0) > 5000: reasons.append("long-form content")
        return f"Selected {agent_type} (conf: {confidence:.0%}) - " + ", ".join(reasons)

    async def process_with_agent_with_metrics(
        self,
        agent_type: str,
        content: str,
        prompt: Optional[str],
        request: Request,
        conversation_id: Optional[str],
        org_id: int,
        request_id: str,
        filename: str = "document",
        routing_confidence: float = 0.0,
    ) -> StreamingResponse:
        """Process with agent and track distributed metrics"""
        start_time = time.time()
        success = True
        try: # type: ignore
            response = await self.process_with_agent(
                agent_type, content, prompt, request, 
                conversation_id, org_id, request_id, filename, routing_confidence
            )
            # Note: record_metric for the full stream is handled inside the stream generator
            return response
        except Exception as e:
            success = False
            await self.metrics.record_metric(agent_type, (time.time()-start_time)*1000, success=False, org_id=org_id)
            raise e

    async def fetch_agents_from_gateway(self) -> List[dict]:
        """Fetch active agents from PHP Gateway via internal API"""
        try:
            # Call the PHP Gateway's /agents/active endpoint with vertical filter
            resp = await auth_internal_client.get(
                "/ai-node/core/agents/active",
                headers={
                    "X-Internal-Secret": INTERNAL_SERVICE_SECRET,
                    "X-Internal-Service": INTERNAL_SERVICE_NAME,
                    "X-Vertical-ID": "aarab",
                    "Accept": "application/json"
                },
                timeout=httpx.Timeout(10.0)
            )
            
            if resp.status_code == 200: # type: ignore
                data = resp.json()
                agents = data.get('agents', [])
                # Filter only AARAB vertical agents
                aarab_agents = [a for a in agents if a.get('vertical_key') == 'aarab' and a.get('status') == 'active']
                logger.info(f"AARAB | Fetched {len(aarab_agents)} agents from PHP Gateway")
                return aarab_agents
            else:
                logger.warning(f"AARAB | Gateway returned {resp.status_code}, using static fallback") # type: ignore
                
        except Exception as e:
            logger.warning(f"AARAB | Failed to fetch agents from gateway: {e}, using static fallback")
            
        return []

    async def load_agents(self) -> None:
        """Load agents from gateway with caching"""
        async with self._lock:
            now = time.time()
            if self.agent_cache and (now - self.last_fetch) < self.cache_ttl: # type: ignore
                return
                
            gateway_agents = await self.fetch_agents_from_gateway()
            
            # Clear existing cache
            self.agent_cache.clear()
            self.capability_aliases.clear()
            
            if gateway_agents:
                # Build cache from gateway data
                for agent in gateway_agents:
                    agent_type = agent.get('agent_type')
                    if not agent_type:
                        continue
                        
                    # Merge with static capability map
                    cap_config = None
                    for cap_key, cap_cfg in self.AGENT_CAPABILITIES.items():
                        if cap_cfg['agent_type'] == agent_type:
                            cap_config = cap_cfg
                            break
                    
                    if not cap_config:
                        # Create minimal config if not in static map
                        cap_config = {
                            'agent_type': agent_type,
                            'name': agent.get('name') or agent_type.replace('_', ' ').title(),
                            'specialties': [],
                            'optimal_for': ['general_documents'],
                            'capability_key': agent_type,
                        }
                    
                    config = cap_config.copy()
                    config['agent_id'] = agent.get('agent_id')
                    config['db_status'] = agent.get('status', 'active')
                    config['db_version'] = agent.get('version') # type: ignore
                    config['description'] = agent.get('description') or config.get('description', '')
                    
                    self.agent_cache[agent_type] = config
                    self.capability_aliases[config.get('capability_key', agent_type)] = agent_type
                    
                logger.info(f"AARAB | Loaded {len(self.agent_cache)} agents from gateway")
            else:
                # Fallback to static capability map only
                for cap_key, cap_cfg in self.AGENT_CAPABILITIES.items():
                    cfg = cap_cfg.copy()
                    cfg['capability_key'] = cap_key
                    self.agent_cache[cap_cfg['agent_type']] = cfg
                    self.capability_aliases[cap_key] = cap_cfg['agent_type']
                logger.info(f"AARAB | Using static fallback for {len(self.agent_cache)} agents")
                
            self.last_fetch = now

    def resolve_agent_type(self, hint: Optional[str]) -> Optional[str]:
        if not hint:
            return None
        key = hint.strip().lower()
        if key in self.capability_aliases: # type: ignore
            return self.capability_aliases[key]
        if key in self.agent_cache:
            return key
        return None

    async def route_document_to_agent(
        self, content: str, filename: str, content_type: str, prompt: Optional[str] = None
    ) -> dict:
        """Legacy routing. For enhanced features, use route_document_to_agent_enhanced."""
        doc_analysis = self._analyze_document(content, filename)
        agent_scores = {
            agent_type: self._calculate_relevance_score(doc_analysis, agent_config) # type: ignore
            for agent_type, agent_config in self.agent_cache.items()
        }
        if not agent_scores:
            default_type = "universal_generalist"
            return {
                "selected_agent": default_type,
                "agent_config": self.agent_cache.get(default_type, {}),
                "confidence": 0.0,
                "document_analysis": doc_analysis,
                "all_scores": {},
            }

        best_agent = max(agent_scores, key=agent_scores.get)
        confidence = agent_scores[best_agent]
 # type: ignore
        logger.info(f"AARAB | Routed to {best_agent} (confidence: {confidence:.2f}) | doc_type={doc_analysis['primary_type']}")
        
        return {
            "selected_agent": best_agent,
            "agent_config": self.agent_cache[best_agent],
            "confidence": confidence,
            "document_analysis": doc_analysis,
            "all_scores": agent_scores,
        }

    def _analyze_document(self, content: str, filename: str) -> dict:
        content_lower = content.lower()
 # type: ignore
        keywords = {
            'legal': ['law', 'legal', 'contract', 'agreement', 'terms', 'compliance', 'regulation', 'statute', 'clause', 'section', 'article'],
            'technical': ['algorithm', 'code', 'function', 'system', 'architecture', 'technical', 'specification', 'api', 'database', 'protocol'],
            'financial': ['financial', 'budget', 'forecast', 'market', 'trend', 'investment', 'revenue', 'profit', 'cost', 'price'],
            'medical': ['medical', 'patient', 'clinical', 'treatment', 'diagnosis', 'health', 'drug', 'therapy', 'disease', 'symptom'],
            'research': ['research', 'study', 'analysis', 'methodology', 'finding', 'conclusion', 'hypothesis', 'experiment', 'data', 'result'],
            'strategic': ['strategy', 'planning', 'roadmap', 'initiative', 'goal', 'objective', 'vision', 'mission', 'framework', 'approach'],
            'creative': ['design', 'creative', 'visual', 'brand', 'marketing', 'campaign', 'advertising', 'content', 'story', 'audience'],
        }
        
        # Calculate density-based scores to prevent bias in longer documents
        word_count = len(content.split()) or 1
        scores = {doc: (sum(content_lower.count(k) for k in words) / word_count) * 100 for doc, words in keywords.items()}
        primary_type = max(scores, key=scores.get) if max(scores.values(), default=0) > 0.05 else "general"
        word_count = len(content.split())
        has_citations = bool(re.search(r"\(?\d{4}\)?", content)) and ( # type: ignore
            "et al" in content_lower or "citation" in content_lower or "references" in content_lower
        )
        return {
            "primary_type": primary_type,
            "word_count": word_count,
            "has_numbers": any(c.isdigit() for c in content),
            "has_dates": bool(re.search(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}", content)),
            "has_citations": has_citations,
            "is_long_document": word_count > 5000,
            "is_technical": scores.get("technical", 0) > 3,
            "is_legal": scores.get("legal", 0) > 3,
            "is_strategic": scores.get("strategic", 0) > 3,
            "detected_keywords": {k: v for k, v in scores.items() if v > 0},
        }

    def _calculate_relevance_score(self, doc_analysis: dict, agent_config: dict) -> float:
        score = 0.0
        agent_type = agent_config.get('agent_type', '')
        specialties = agent_config.get('specialties', []) # type: ignore
        primary_type = doc_analysis["primary_type"]

        optimal_mapping = {
            'legal': ['truth_seeker', 'precision_analyst'],
            'technical': ['reasoner', 'universal_generalist'],
            'financial': ['temporal_forecaster', 'trend_predictor'],
            'medical': ['precision_analyst', 'reasoner'],
            'research': ['trend_predictor', 'breakthrough_monitor', 'multimodal_synthesizer'],
            'strategic': ['strategic_foresight', 'temporal_forecaster'],
            'creative': ['multimodal_visualizer', 'viz_architect', 'ideator'],
            'general': ['universal_generalist', 'summarizer'],
        }
        
        if agent_type in optimal_mapping.get(primary_type, []): # type: ignore
            score += 0.5
            
        if doc_analysis["is_long_document"] and agent_type in ("summarizer", "multimodal_synthesizer"):
            score += 0.2
            
        if doc_analysis['is_technical'] and 'technical' in str(specialties).lower():
            score += 0.3
            
        if doc_analysis['is_legal'] and 'validation' in str(specialties).lower():
            score += 0.3
            
        if doc_analysis['is_strategic'] and 'strategic' in str(specialties).lower():
            score += 0.3
            
        if doc_analysis['has_citations'] and any(s in str(specialties).lower() for s in ['source', 'provenance']):
            score += 0.2

        agent_bonus = {
            'truth_seeker': 0.2 if doc_analysis['has_citations'] else 0,
            'summarizer': 0.2 if doc_analysis['is_long_document'] else 0,
            'reasoner': 0.2 if doc_analysis['is_technical'] else 0,
            'ideator': 0.2 if primary_type == 'creative' else 0,
            'temporal_forecaster': 0.2 if doc_analysis['has_dates'] else 0,
            'precision_analyst': 0.3 if doc_analysis['is_legal'] or doc_analysis['is_technical'] else 0,
        }
        
        score += agent_bonus.get(agent_type, 0)
        return min(score, 1.0)

    def _build_agent_prompt(self, agent_config: dict) -> str:
        """Build specialized system prompt for each agent"""
        agent_type = agent_config.get('agent_type', '')
        name = agent_config.get('name', agent_type)
        specialties = ", ".join(agent_config.get('specialties', []))
        
        prompts = {
            'truth_seeker': f"""You are {name}, the Truth-Seeking & Validation agent.
Your mission: Multi-source validation with full provenance.
Focus on: fact-checking, source verification, bias detection, and complete provenance for all claims.
Provide confidence scores and flag any unverified information.""",

            'reasoner': f"""You are {name}, the Complex Reasoning agent.
Your mission: Causal dissection of scientific and technical problems.
Provide step-by-step logical reasoning, identify root causes, and highlight breakthrough insights.
Structure your analysis with clear causal chains.""",

            'ideator': f"""You are {name}, the Idea Generation agent.
Your mission: Hypothesis creation from raw and sparse data.
Generate novel hypotheses, unconventional perspectives, and innovation sparks.
Be creative but ground suggestions in available evidence.""",

            'summarizer': f"""You are {name}, the Elite Summarization agent.
Your mission: Executive and public-facing briefs.
Distill complex information into concise, high-impact communication.
Focus on key insights, takeaways, and actionable recommendations.""",

            'temporal_forecaster': f"""You are {name}, the Temporal Analysis & Forecasting agent.
Analyze historical patterns, identify emerging trends, and create realistic future projections.
Provide strategic roadmaps and identify inflection points.""",

            'universal_generalist': f"""You are {name}, the Cross-Domain Universal Intelligence agent.
Provide comprehensive, balanced analysis across domains.
Integrate multiple perspectives and adapt your approach to the subject matter.""",
        }
        
        default_prompt = f"""You are {name}, a specialized AI agent.
Capabilities: {specialties}
Apply your signature expertise with precision and provide actionable insights.
Adapt your response to the specific document type and user request."""
        
        return prompts.get(agent_type, default_prompt)

    async def process_with_agent(
        self,
        agent_type: str,
        content: str,
        prompt: Optional[str],
        request: Request,
        conversation_id: Optional[str],
        org_id: int,
        request_id: str,
        filename: str = "document",
        routing_confidence: float = 0.0,
    ) -> StreamingResponse:
        """Process document using a specific AARAB agent"""
        
        if agent_type in ('multimodal_visualizer', 'aetheris'):
            if "infographic" in (prompt or "").lower() or "visualization" in (prompt or "").lower(): # type: ignore
                viz_data = await AetherisVisualizationGenerator.generate_infographic(
                    {"title": filename, "content": content[:2000]}, 
                    request_id
                )
                
                async def aetheris_stream():
                    yield f"data: {json.dumps(viz_data)}\n\n"
                    yield "data: [DONE]\n\n"
                
                return StreamingResponse(aetheris_stream(), media_type="text/event-stream")

        agent_config = self.agent_cache.get(agent_type)
        if not agent_config: # type: ignore
            logger.warning(f"AARAB | Agent {agent_type} not found, falling back to universal_generalist")
            agent_config = self.agent_cache.get('universal_generalist')
            if not agent_config:
                raise HTTPException(status_code=503, detail="AARAB agent suite unavailable")

        system_prompt = self._build_agent_prompt(agent_config)
        task_prompt = prompt or "Process this document according to your specialized capabilities and provide comprehensive analysis."

        system_message = (
            f"{system_prompt}\n\n"
            "## Output Format:\n"
            "- Brief acknowledgment of your agent role\n"
            "- Structured analysis aligned with your expertise\n"
            "- Key findings, insights, and recommendations\n"
            "- Confidence score and any limitations or caveats\n"
        )

        user_message = (
            f"## Agent: {agent_config.get('name', agent_type)} ({agent_type})\n\n"
            f"## Document: {filename}\n\n"
            f"{content[:8000]}\n\n"
            f"## Task:\n{task_prompt}\n"
        )

        full_messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ]

        stream_request = StreamingChatRequest(
            messages=full_messages,
            model=LLM_MODEL,
            stream=True,
            conversation_id=conversation_id,
        )
        
        identity_key = request_identity_key(request, getattr(request.state, 'user', {}))
        resolved_cid = await get_conversation_id(identity_key, conversation_id, org_id) # type: ignore
        start_time = time.time()

        # Create agent info for progress tracking
        agent_info = {
            "name": agent_config.get("name", agent_type),
            "agent_type": agent_type,
            "current_action": "analyzing document",
            "specialties": agent_config.get("specialties", []),
            "confidence": routing_confidence
        }

        async def aarab_stream_generator():
            agent_name = agent_config.get("name", agent_type)
 # type: ignore
            # Send initial detailed agent status
            yield f"data: {json.dumps({
                'type': 'status',
                'content': f'AARAB agent {agent_name} is initializing...',
                'agent': agent_name,
                'agent_type': agent_type,
                'step': 'initializing',
                'progress': 0,
                'conversation_id': resolved_cid
            })}\n\n"
            
            # Simulated pre-processing steps for perceived performance
            for step_msg, progress in [("Extracting document content", 10), ("Analyzing logical structure", 20)]:
                yield f"data: {json.dumps({ # type: ignore
                    'type': 'status', 
                    'content': f'{agent_name}: {step_msg}...', 
                    'agent': agent_name, 
                    'progress': progress
                })}\n\n"
                await asyncio.sleep(0.5)

            try:
                async for event in _internal_ollama_stream_generator(
                    base_model=LLM_MODEL,
                    full_messages=full_messages,
                    body=stream_request,
                    request_id=request_id,
                    identity_key=identity_key,
                    conversation_id=resolved_cid,
                    user_content=task_prompt[:200],
                    request=request,
                    org_id=org_id,
                    agent_info=agent_info
                ):
                    if event["type"] == "status": # type: ignore
                        yield f"data: {json.dumps(event)}\n\n"
                    if event["type"] == "token":
                        token_text = sanitize_stream_token(event.get('token', ''))
                        yield f"data: {json.dumps({'type': 'token', 'content': token_text, 'conversation_id': resolved_cid})}\n\n"
                    elif event["type"] == "done":
                        duration = time.time() - start_time
                        metrics = self.agent_metrics[agent_type]
                        metrics['tasks_processed'] += 1 # type: ignore
                        prev_total = metrics['tasks_processed'] - 1
                        if prev_total > 0:
                            metrics['avg_response_time'] = (metrics['avg_response_time'] * prev_total + duration) / metrics['tasks_processed']
                        else:
                            metrics['avg_response_time'] = duration
                        
                        yield f"data: {json.dumps({'type': 'complete', 'usage': event.get('usage'), 'agent': agent_name})}\n\n"
                        yield "data: [DONE]\n\n"
                    elif event["type"] == "error":
                        self.agent_metrics[agent_type]['success_rate'] *= 0.95 # type: ignore
                        yield f"data: {json.dumps({'type': 'error', 'error': event.get('error')})}\n\n"
            except Exception as e:
                self.agent_metrics[agent_type]['success_rate'] *= 0.95
                logger.error(f"AARAB | Agent {agent_type} stream error: {e}")
                raise

        resp = StreamingResponse(aarab_stream_generator(), media_type="text/event-stream")
        resp.headers.update({
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Content-Security-Policy": "default-src 'none'; script-src 'none'; img-src 'self'; connect-src 'self'; style-src 'self'; frame-ancestors 'none';",
            "X-Agent-Used": agent_config.get("name", agent_type),
            "X-Agent-Type": agent_type,
            "X-Agent-Confidence": f"{routing_confidence:.2f}",
        })
        return resp

    async def get_agent_metrics(self) -> dict:
        """Get performance metrics for all AARAB agents"""
        return {
            agent_type: {
                "tasks_processed": metrics['tasks_processed'], # type: ignore
                "avg_response_time_ms": round(metrics['avg_response_time'] * 1000, 2),
                "success_rate": round(metrics['success_rate'], 3),
            }
            for agent_type, metrics in self.agent_metrics.items()
            if metrics['tasks_processed'] > 0
        }

    async def get_agent_list(self) -> List[dict]:
        """Get list of all available AARAB agents with their capabilities"""
        await self.load_agents()
        
        agents: List[dict] = [] # type: ignore
        for agent_type, config in self.agent_cache.items():
            agents.append({
                "agent_type": agent_type,
                "capability_key": config.get('capability_key'),
                "name": config.get('name'),
                "agent_id": config.get('agent_id'),
                "specialties": config.get('specialties', []),
                "optimal_for": config.get('optimal_for', []),
                "version": config.get('db_version', 'AARAB v4.0 (70B)'),
                "status": config.get('db_status', 'active'),
                "description": config.get('description', ''),
            })
        return agents

aarab_orchestrator = AARABAgentOrchestrator()

async def store_document_chunks(doc_id: int, org_id: int, chunks: List[str]):
    """
    Production Persistence: Stores extracted text chunks into 'legal_document_chunks'.
    This is critical for multi-turn RAG durability.
    """ # type: ignore
    logger.info(f"DB | Storing {len(chunks)} chunks for doc_id={doc_id} | Org: {org_id}")
    # In production, this would perform a batch INSERT and generate embeddings
    pass

async def store_chunk_with_embedding(doc_id: int, legal_organization_id: int, content: str, embedding: list[float], page_number: int | None, chunk_index: int):
    """
    DEPRECATED: Use store_document_chunks for batch processing.
    Placeholder for individual chunk storage in legal_document_chunks.
    """ # type: ignore
    logger.info(f"DB | Individual chunk storage for index {chunk_index}")
    pass

class DocumentAnalysisJob(BaseModel):
    job_id: str
    status: str

@app.options("/{path:path}")
async def cors_preflight(path: str):
    """Explicit OPTIONS handler for preflight requests to bypass custom middlewares."""
    return JSONResponse(content={})

class IngestionService:
    """REUSABLE CORE SERVICE: Handles multi-modal document extraction and registration."""
    
    @staticmethod
    async def extract_content(file_bytes: bytes, filename: str, content_type: str, request_id: str) -> str:
        file_ext = os.path.splitext(filename)[1].lower()
        if file_ext == '.pdf' or content_type.startswith("application/pdf"): # type: ignore
            return await asyncio.get_event_loop().run_in_executor(
                None, _extract_pdf_text_safe, file_bytes, request_id
            )
        # Handle other types (docx, text, etc) using logic from _index_document_internal
        return file_bytes[:8000].decode("utf-8", errors="ignore")

    @staticmethod
    def get_vertical_config(vertical: str) -> dict:
        return VERTICAL_PROMPTS.get(vertical, VERTICAL_PROMPTS["core"])

@app.post("/ingest", tags=["CORE Ingestion"])
async def platform_ingest(
    request: Request,
    file: UploadFile = File(...),
    vertical: str = Form("core"),
    prompt: Optional[str] = Form(None),
    conversation_id: Optional[str] = Form(None, alias="workstream_id"),
    workstream_id: Optional[str] = Form(None),
):
    """
    NEW: CORE API for multi-vertical ingestion. # type: ignore
    Generalizes document analysis beyond just legal files.
    """
    header_vertical = getattr(request.state, "vertical_id", None)
    if vertical == "core" and header_vertical:
        vertical = header_vertical
    return await analyze_document(
        request=request,
        file=file,
        prompt=prompt,
        vertical=vertical,
        conversation_id=conversation_id or workstream_id,
        workstream_id=workstream_id,
    )

@app.post("/documents/batch/analyze", tags=["CORE Ingestion"])
async def batch_analyze_documents(
    request: Request,
    files: List[UploadFile] = File(...),
    prompt: Optional[str] = Form(None),
    vertical: str = Form("core"),
    conversation_id: Optional[str] = Form(None, alias="workstream_id"),
    user: dict = Depends(enforce_user_access),
):
    """
    Concurrent batch processing for multiple documents (max 5). # type: ignore
    Registers files and dispatches background indexing tasks.
    """
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    org_id = int(getattr(request.state, "legal_organization_id", 1))
    
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > 5:
        raise HTTPException(status_code=400, detail="Batch limit exceeded (max 5 files)")
        
    await enforce_org_document_upload_limit(org_id, request_id)
 # type: ignore
    batch_results = []
    for file in files:
        file_path, file_size, hash_sha256 = await save_upload_file_to_tempfile(file)
        filename = file.filename or "unknown"
        content_type = file.content_type or "application/octet-stream"

        validate_upload_file_path(file_path, filename, content_type)

        doc_id = await register_legal_document(
            task_id=0, org_id=org_id, filename=filename, 
            mime=content_type, size=file_size, hash_sum=hash_sha256, 
            request_id=request_id
        )

        safe_send_task(
            "process_legal_doc",
            args=[file_path, filename, content_type, org_id, doc_id, request_id],
        )

        batch_results.append({"filename": filename, "doc_id": doc_id, "status": "queued"})
        
    return JSONResponse(
        status_code=202,
        content={
            "success": True,
            "batch_id": request_id,
            "documents": batch_results,
            "message": f"Successfully queued {len(files)} documents for indexing."
        }
    )

@app.post("/documents/analyze", tags=["Legal Infrastructure"])
async def _aarab_process_document_core(
    request: Request,
    user: dict,
    file_path: str,
    file_size: int,
    hash_sha256: str,
    filename: str,
    content_type: str,
    prompt: Optional[str],
    conversation_id: Optional[str],
    org_id: int,
    request_id: str,
    agent_hint: Optional[str] = None,
    auto_route: bool = True,
) -> StreamingResponse:
    """Shared AARAB agent routing, indexing queue, and streaming inference.""" # type: ignore

    validate_upload_file_path(file_path, filename, content_type)
    if file_size > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 50MB)")

    extracted_text = ""
    if content_type.startswith("application/pdf") or filename.lower().endswith(".pdf"):
        extracted_text = await asyncio.get_event_loop().run_in_executor( # type: ignore
            None, _extract_pdf_text_safe_from_path, file_path, request_id, 20
        )
    else:
        extracted_text = await asyncio.get_event_loop().run_in_executor(
            None, read_file_head_text, file_path, 10000
        )

    if not extracted_text.strip():
        # Robustness Fix: Handle scanned/image-only PDFs gracefully instead of crashing
        logger.warning(f"{request_id} | AARAB | Text extraction yielded 0 characters for {filename}")
        extracted_text = (
            f"[SYSTEM NOTICE: Direct text extraction for '{filename}' yielded no results. "
            "This usually indicates a scanned document or image-only PDF. "
            "The AARAB background engine has been dispatched to perform deep OCR indexing. "
            "For this immediate session, I will proceed with analysis based on the document metadata and your instructions.]"
        )
        # If the user didn't provide a prompt, we need one to start the agent
        if not prompt:
            prompt = f"Analyze the document '{filename}' once OCR processing is complete."

    # Load AARAB agents # type: ignore
    await aarab_orchestrator.load_agents()

    routing_info = None
    resolved_hint = aarab_orchestrator.resolve_agent_type(agent_hint)
    if resolved_hint:
        selected_agent = resolved_hint
        logger.info(f"{request_id} | AARAB | Using hinted agent: {selected_agent}")
    elif auto_route:
        routing_info = await aarab_orchestrator.route_document_to_agent_enhanced(
            extracted_text, filename, content_type, prompt
        )
        selected_agent = routing_info["selected_agent"]
        logger.info(
            f"{request_id} | AARAB | Auto-routed to {selected_agent} "
            f"(confidence: {routing_info['confidence']:.2f}) | Reason: {routing_info.get('routing_reason')}"
        )
    else:
        selected_agent = "universal_generalist"
        logger.info(f"{request_id} | AARAB | Default agent: Omnis")

    lock_key = f"ingest:aarab:{org_id}:{hash_sha256[:32]}" # type: ignore
    async with RedisLock(redis_client, lock_key, ttl=90):
        doc_id = await register_legal_document(
            task_id=int(request.headers.get("X-Task-ID", 0)),
            org_id=org_id,
            filename=filename,
            mime=content_type,
            size=file_size,
            hash_sum=hash_sha256,
            request_id=request_id,
        )
        try:
            routing_payload = json.dumps(routing_info) if routing_info else None
            safe_send_task(
                "process_aarab_document",
                args=[
                    file_path,
                    filename,
                    content_type,
                    org_id,
                    doc_id,
                    request_id,
                    selected_agent,
                    routing_payload,
                ],
            )
        except Exception as e: # type: ignore
            logger.warning(f"{request_id} | AARAB | Queue failed (non-fatal): {e}")

    confidence = routing_info["confidence"] if routing_info else (1.0 if resolved_hint else 0.0)

    # CRITICAL FIX: Removed immediate os.unlink(file_path).
    # The file MUST persist until the Celery worker (process_aarab_document) 
    # reads it. The worker's _file_payload_to_bytes helper already handles the unlink.

    return await aarab_orchestrator.process_with_agent_with_metrics(
        agent_type=selected_agent,
        content=extracted_text,
        prompt=prompt,
        request=request,
        conversation_id=conversation_id,
        org_id=org_id,
        request_id=request_id,
        filename=filename,
        routing_confidence=confidence,
    )

async def analyze_document(
    request: Request,
    file: UploadFile = File(...),
    prompt: Optional[str] = Form(None),
    vertical: str = Form("general"),
    conversation_id: Optional[str] = Form(None, alias="workstream_id"),
    workstream_id: Optional[str] = Form(None),
):
    user = await enforce_user_access(request) # type: ignore
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    try:
        header_vertical = getattr(request.state, "vertical_id", None)
        if vertical in ("general", "core") and header_vertical:
            vertical = header_vertical

        legal_organization_id = int(getattr(request.state, "legal_organization_id", 0))
        if legal_organization_id <= 0: # type: ignore
            legal_organization_id = int(user.get("legal_organization_id") or user.get("organization_id") or 1)

        cid = conversation_id or workstream_id

        await enforce_org_document_upload_limit(legal_organization_id, request_id)

        file_path, file_size, hash_sha256 = await save_upload_file_to_tempfile(file)
        filename = file.filename or "unknown.pdf"
        content_type = file.content_type or "application/pdf"

        if vertical == "aarab":
            return await _aarab_process_document_core(
                request=request,
                user=user,
                file_path=file_path,
                file_size=file_size,
                hash_sha256=hash_sha256,
                filename=filename,
                content_type=content_type,
                prompt=prompt,
                conversation_id=cid,
                org_id=legal_organization_id,
                request_id=request_id,
                auto_route=True,
            )

        collection_name = get_vector_collection(vertical)
 # type: ignore
        validate_upload_file_path(file_path, filename, content_type)

        if file_size > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="File too large (max 50MB)")

        # Grace Mode: Strict Document Enforcement (Production Fix: Respect is_verified)
        is_verified = bool(user.get("is_verified")) or user.get("kyc_status") in ["verified", "approved", "verified_institutional"]
        if not is_verified and user.get("role") != "system": # type: ignore
            size_mb = file_size / (1024 * 1024)
            if size_mb > GRACE_MAX_DOC_SIZE_MB * 1.5:  # Slightly more lenient for testing
                raise HTTPException(status_code=403, detail={
                    "error": "Document too large for grace mode",
                    "message": f"Pending accounts limited to ~{GRACE_MAX_DOC_SIZE_MB}MB. Please verify.",
                    "action": "https://arybit.co.ke/account/kyc"
                })
            
            if redis_client:
                today = datetime.now(timezone.utc).date().isoformat()
                daily_key = f"grace:docs:{user.get('user_id')}:{today}" # type: ignore
                count = await safe_redis_op(redis_client.get(daily_key), default="0")
                
                if int(count or 0) >= GRACE_MAX_DOCUMENTS_PER_DAY:
                    raise HTTPException(status_code=403, detail={
                        "error": "Daily document limit reached",
                        "message": f"Grace mode is limited to {GRACE_MAX_DOCUMENTS_PER_DAY} documents per day.",
                        "action": "https://arybit.co.ke/account/kyc"
                    })
                
                await safe_redis_op(redis_client.incr(daily_key))
                await safe_redis_op(redis_client.expire(daily_key, 86400))

        # Early size validation (already present) + content-type normalization
        if content_type.startswith("application/pdf") or filename.lower().endswith(('.pdf', '.docx', '.doc')):
            # Force correct MIME for docx if client sends generic
            if filename.lower().endswith('.docx') and not content_type.startswith('application/vnd.openxmlformats'):
                content_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'

        # Quick extraction for immediate response # type: ignore
        quick_text = ""
        if content_type.startswith("application/pdf") or filename.lower().endswith(".pdf"):
            quick_text = await asyncio.get_event_loop().run_in_executor(
                None, _extract_pdf_text_safe_from_path, file_path, request_id, 5
            )

        # Distributed deduplication lock
        lock_key = f"ingest:{vertical}:{legal_organization_id}:{hash_sha256[:32]}"
        async with RedisLock(redis_client, lock_key, ttl=90): # type: ignore
            doc_id = await register_legal_document(
                task_id=int(request.headers.get("X-Task-ID", 0)),
                org_id=legal_organization_id,
                filename=filename,
                mime=content_type,
                size=file_size,
                hash_sum=hash_sha256,
                request_id=request_id
            )

            # Queue background indexing
            try:
                safe_send_task(
                    "process_legal_doc",
                    args=[file_path, filename, content_type, legal_organization_id, doc_id, request_id],
                )
                try:
                    loop = asyncio.get_event_loop()
                    _, _, reserved = await loop.run_in_executor(None, _celery_worker_health_sync)
                    depth = sum(len(tasks) for tasks in (reserved or {}).values())
                    document_queue_depth.set(depth)
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"{request_id} | CELERY | Queue failed (non-fatal): {e}")

        # Build prompt
        v_config = get_vertical_config(vertical) # type: ignore
        context = quick_text[:8000] if quick_text else read_file_head_text(file_path, 6000)

        augmented_prompt = (
            f"SOURCE: {filename}\n"
            f"Context Type: {vertical.upper()}\n\n"
            f"{v_config['system']}\n\n"
            f"CONTENT:\n{context}\n\n"
            f"TASK: {prompt or 'Summarize findings and extract actionable intelligence based on organizational context.'}"
        )

        # MOVE THE GRACE LIMITS CHECK HERE - ONLY FOR UNVERIFIED USERS
        if not is_verified and user.get("role") != "system": # type: ignore
            await _apply_grace_limits(
                prompt_length=len(augmented_prompt),
                estimated_tokens=estimate_tokens(augmented_prompt) + 800,
                user=user,
                request=request,
                model=LLM_MODEL
            )

        stream_request = StreamingChatRequest(
            prompt=augmented_prompt,
            model=LLM_MODEL,
            stream=True,
            conversation_id=cid
        )

        identity_key = request_identity_key(request, user) # type: ignore
        resolved_cid = await get_conversation_id(identity_key, cid, legal_organization_id)

        async def document_stream_generator():
            yield f"data: {json.dumps({'type': 'status', 'content': f'Ingesting {filename} into {vertical} memory...', 'conversation_id': resolved_cid})} \n\n"
            await asyncio.sleep(0.4)

            async for event in _internal_ollama_stream_generator(
                base_model=LLM_MODEL,
                full_messages=[{"role": "user", "content": augmented_prompt}],
                body=stream_request,
                request_id=request_id,
                identity_key=identity_key,
                conversation_id=resolved_cid,
                user_content=prompt or "Document analysis",
                request=request, # type: ignore
                org_id=legal_organization_id
            ):
                if event["type"] == "token":
                    yield f"data: {json.dumps({'type': 'token', 'content': event['token'], 'conversation_id': cid})}\n\n"
                elif event["type"] == "done":
                    yield f"data: {json.dumps({'type': 'complete', 'usage': event['usage']})}\n\n"
                    yield "data: [DONE]\n\n"

        resp = StreamingResponse(document_stream_generator(), media_type="text/event-stream")
        resp.headers.update({
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no"
        })
        return resp

    except HTTPException:
        raise # type: ignore
    except Exception as e:
        logger.error(f"{request_id} | INGEST | Critical failure: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Intelligence ingestion engine encountered an error")

# ============================================================
# CHUNKED UPLOAD CORE (multi-tenant safe)
# ============================================================

class ChunkUploadInit(BaseModel):
    upload_id: str = Field(..., pattern=r'^[a-zA-Z0-9_\-]{8,64}$')
    filename: str
    file_size: int = Field(..., gt=0, le=500 * 1024 * 1024)  # 500MB max
    total_chunks: int = Field(..., gt=0, le=10000)
    chunk_size: int = Field(..., gt=0)
    metadata: Optional[dict] = None


# Global fallback for single-worker / no-Redis mode
chunked_uploads: dict = {}
chunked_uploads_lock = asyncio.Lock()


async def cleanup_abandoned_uploads():
    """Background task to remove stalled chunked upload sessions older than 1 hour."""
    while True:
        try:
            await asyncio.sleep(3600)  # Run every hour
            now = time.time()
            deleted_count = 0

            # Cleanup Redis-based sessions
            if redis_client: # type: ignore
                cursor = 0
                while True:
                    cursor, keys = await redis_client.scan(cursor, match="upload_session:*", count=100)
                    for key in keys:
                        try:
                            key_str = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
                            data = await redis_client.get(key_str)
                            if not data:
                                continue
                            session = json.loads(data)
                            if now - session.get("created_at", 0) > 7200:  # 2 hour timeout # type: ignore
                                session_key = key_str.split(":", 1)[1] # type: ignore
                                await redis_client.delete(key_str)
                                await redis_client.delete(f"upload_chunks:{session_key}")
                                
                                # Clean up temp file if it exists
                                temp_path = session.get("temp_file_path")
                                if temp_path and os.path.exists(temp_path):
                                    try:
                                        os.unlink(temp_path)
                                    except Exception as e:
                                        logger.warning(f"UPLOAD CLEANUP | Failed to delete {temp_path}: {e}")
                                
                                deleted_count += 1
                                logger.info(f"UPLOAD CLEANUP | Evicted stale Redis session: {session_key}")
                        except Exception as e:
                            logger.warning(f"UPLOAD CLEANUP | Redis key error: {e}")
                            continue
                    if cursor == 0:
                        break

            # Cleanup local memory sessions (fallback for single-worker mode)
            async with chunked_uploads_lock:
                expired_ids = [
                    sid for sid, session in chunked_uploads.items() # type: ignore
                    if now - session.get("created_at", 0) > 7200
                ]
                for sid in expired_ids:
                    session = chunked_uploads.pop(sid)
                    temp_path = session.get("temp_file_path")
                    if temp_path and os.path.exists(temp_path):
                        try:
                            os.unlink(temp_path)
                        except Exception as e:
                            logger.warning(f"UPLOAD CLEANUP | Failed to delete {temp_path}: {e}")
                    deleted_count += 1
                    logger.info(f"UPLOAD CLEANUP | Evicted stale memory session: {sid}")

            if deleted_count > 0:
                logger.info(f"UPLOAD CLEANUP | Total cleaned: {deleted_count} stale sessions")

        except Exception as e:
            logger.error(f"UPLOAD CLEANUP | Task error: {e}", exc_info=True)


@app.post("/aarab/process/init", tags=["AARAB Intelligence"])
async def init_chunked_upload(
    request: Request,
    init_data: ChunkUploadInit,
    user: dict = Depends(enforce_user_access)
):
    """Initialize a chunked upload session for large documents."""
    user_id = user.get("user_id") # type: ignore
    if not user_id:
        raise HTTPException(status_code=401, detail="User authentication required")

    org_id = int(getattr(request.state, "legal_organization_id", 0) or user.get("legal_organization_id", 1))

    session_key = f"{user_id}:{init_data.upload_id}"
    temp_path = os.path.join(tempfile.gettempdir(), f"aarab_upload_{session_key.replace(':', '_')}")

    session_data = {
        "user_id": user_id,
        "org_id": org_id,
        "filename": init_data.filename,
        "file_size": init_data.file_size,
        "total_chunks": init_data.total_chunks,
        "chunk_size": init_data.chunk_size,
        "metadata": init_data.metadata or {},
        "temp_file_path": temp_path,
        "created_at": time.time(),
    }

    try:
        # Pre-allocate file to prevent fragmentation
        with open(temp_path, "wb") as f:
            f.truncate(init_data.file_size) # type: ignore
        logger.info(f"UPLOAD INIT | Pre-allocated {init_data.file_size} bytes at {temp_path}")
    except Exception as e:
        logger.error(f"UPLOAD INIT | Failed to pre-allocate temp file: {e}")
        raise HTTPException(status_code=500, detail="Failed to initialize storage")

    if redis_client:
        await safe_redis_op(
            redis_client.setex(f"upload_session:{session_key}", 7200, json.dumps(session_data)) # type: ignore
        )
        await safe_redis_op(redis_client.delete(f"upload_chunks:{session_key}"))
        logger.info(f"UPLOAD INIT | Redis session stored: {session_key}")
    else:
        async with chunked_uploads_lock:
            session_data["received_chunks"] = set()
            chunked_uploads[session_key] = session_data
        logger.info(f"UPLOAD INIT | Memory session stored: {session_key}")

    return {
        "status": "initialized",
        "upload_id": init_data.upload_id,
        "session_key": session_key,
        "total_chunks": init_data.total_chunks
    }


@app.post("/aarab/process/chunk", tags=["AARAB Intelligence"])
async def upload_chunk(
    request: Request,
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    chunk: UploadFile = File(...),
    user: dict = Depends(enforce_user_access)
):
    """Receive and store a single document chunk."""
    user_id = user.get("user_id") # type: ignore
    if not user_id:
        raise HTTPException(status_code=401, detail="User authentication required")

    if chunk_index < 0 or chunk_index >= 10000:
        raise HTTPException(status_code=400, detail="Invalid chunk index")

    session_key = f"{user_id}:{upload_id}"

    # Retrieve session (Redis first, then memory)
    upload = None
    from_redis = False

    if redis_client:
        data = await safe_redis_op(redis_client.get(f"upload_session:{session_key}"))
        if data:
            upload = json.loads(data) # type: ignore
            from_redis = True

    if not upload:
        async with chunked_uploads_lock:
            upload = chunked_uploads.get(session_key)

    if not upload:
        logger.warning(f"UPLOAD CHUNK | Session not found: {session_key}")
        raise HTTPException(status_code=404, detail="Upload session not found or expired") # type: ignore

    # Tenant isolation check
    current_org = int(getattr(request.state, "legal_organization_id", 0))
    if upload.get("org_id") != current_org and current_org != 0:
        logger.warning(f"UPLOAD CHUNK | Org mismatch: session_org={upload.get('org_id')}, request_org={current_org}")
        raise HTTPException(status_code=403, detail="Organization access denied")

    temp_path = upload["temp_file_path"]
    if not os.path.exists(temp_path):
        logger.error(f"UPLOAD CHUNK | Temp file missing: {temp_path}") # type: ignore
        raise HTTPException(status_code=500, detail="Temporary file lost")

    # Validate chunk bounds
    offset = chunk_index * upload["chunk_size"]
    chunk_data = await chunk.read()
    
    if offset < 0 or offset + len(chunk_data) > upload["file_size"] + 8192:
        logger.error(f"UPLOAD CHUNK | Bounds violation: offset={offset}, size={len(chunk_data)}, file_size={upload['file_size']}")
        raise HTTPException(status_code=400, detail="Chunk exceeds file bounds")

    # Write chunk at specific offset (thread-safe)
    try:
        with open(temp_path, "r+b") as f:
            f.seek(offset) # type: ignore
            f.write(chunk_data)
    except Exception as e:
        logger.error(f"UPLOAD CHUNK | Write failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to write chunk")

    # Track received chunks
    if from_redis and redis_client:
        await safe_redis_op(redis_client.sadd(f"upload_chunks:{session_key}", chunk_index))
        await safe_redis_op(redis_client.expire(f"upload_chunks:{session_key}", 7200)) # type: ignore
        await safe_redis_op(redis_client.expire(f"upload_session:{session_key}", 7200))
        received = await safe_redis_op(redis_client.scard(f"upload_chunks:{session_key}"), default=0)
    else:
        async with chunked_uploads_lock:
            upload = chunked_uploads.get(session_key)
            if upload:
                upload.setdefault("received_chunks", set()).add(chunk_index)
                received = len(upload["received_chunks"])
            else:
                received = 0

    progress = round(received / upload["total_chunks"] * 100, 1) if upload["total_chunks"] > 0 else 0 # type: ignore
    
    logger.debug(f"UPLOAD CHUNK | {session_key} | chunk={chunk_index} | progress={progress}%")
    
    return {
        "status": "ok",
        "chunk_index": chunk_index,
        "received": received,
        "total": upload["total_chunks"],
        "progress": progress,
    }


@app.post("/aarab/process/finalize", tags=["AARAB Intelligence"])
async def finalize_chunked_upload(
    request: Request,
    finalize_data: dict,
    user: dict = Depends(enforce_user_access)
):
    """Assemble chunks and start AARAB processing."""
    upload_id = finalize_data.get("upload_id")
    if not upload_id:
        raise HTTPException(status_code=400, detail="upload_id required")

    user_id = user.get("user_id") # type: ignore
    if not user_id:
        raise HTTPException(status_code=401, detail="User authentication required")

    session_key = f"{user_id}:{upload_id}"
    upload = None
    received_chunks = set()

    # Retrieve session (Redis first, then memory)
    if redis_client:
        data = await safe_redis_op(redis_client.get(f"upload_session:{session_key}"))
        if data:
            upload = json.loads(data) # type: ignore
            chunks = await safe_redis_op(redis_client.smembers(f"upload_chunks:{session_key}"))
            received_chunks = {int(c) for c in chunks or []}

    if not upload:
        async with chunked_uploads_lock:
            upload = chunked_uploads.pop(session_key, None)
            if upload:
                received_chunks = upload.get("received_chunks", set())

    if not upload:
        logger.warning(f"UPLOAD FINALIZE | Session not found: {session_key}")
        raise HTTPException(status_code=404, detail="Upload session not found or expired") # type: ignore

    # Verify all chunks received
    if len(received_chunks) != upload["total_chunks"]:
        missing = upload["total_chunks"] - len(received_chunks)
        logger.warning(f"UPLOAD FINALIZE | Incomplete upload: {missing}/{upload['total_chunks']} chunks missing")
        raise HTTPException(
            status_code=400,
            detail=f"Incomplete upload: missing {missing} chunks"
        )

    # Cleanup session state
    if redis_client:
        await safe_redis_op(redis_client.delete(f"upload_session:{session_key}")) # type: ignore
        await safe_redis_op(redis_client.delete(f"upload_chunks:{session_key}"))

    file_path = upload["temp_file_path"]
    if not file_path or not os.path.exists(file_path):
        logger.error(f"UPLOAD FINALIZE | Temp file missing: {file_path}")
        raise HTTPException(status_code=500, detail="Temporary storage lost")

    # Calculate final hash for deduplication
    file_hash = safe_file_hash(file_path)

    logger.info(f"UPLOAD FINALIZE | {session_key} | Complete, size={upload['file_size']}, hash={file_hash[:16]}...")

    return await _aarab_process_document_core(
        request=request,
        user=user,
        file_path=file_path,
        file_size=upload["file_size"],
        hash_sha256=file_hash,
        filename=upload["filename"],
        content_type="application/pdf" if upload["filename"].lower().endswith(".pdf") else "application/octet-stream",
        prompt=upload["metadata"].get("prompt"),
        conversation_id=upload["metadata"].get("conversation_id"),
        org_id=upload["org_id"],
        request_id=getattr(request.state, "request_id", str(uuid.uuid4()))
    )


@app.get("/aarab/process/status/{upload_id}", tags=["AARAB Intelligence"])
async def get_upload_status(
    upload_id: str,
    request: Request,
    user: dict = Depends(enforce_user_access)
):
    """Get upload progress."""
    user_id = user.get("user_id") # type: ignore
    if not user_id:
        raise HTTPException(status_code=401, detail="User authentication required")

    session_key = f"{user_id}:{upload_id}"
    session = None
    received = 0
    source = None

    # Try Redis
    if redis_client:
        try:
            data = await safe_redis_op(redis_client.get(f"upload_session:{session_key}"))
            if data: # type: ignore
                session = json.loads(data)
                received = await safe_redis_op(
                    redis_client.scard(f"upload_chunks:{session_key}"), default=0
                )
                source = "redis"
                await safe_redis_op(redis_client.expire(f"upload_session:{session_key}", 3600))
            else:
                source = "redis"
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in Redis upload session: {session_key}")
        except Exception as exc:
            logger.warning(f"Redis status lookup failed for {session_key}: {exc}")

    # Fallback to memory
    if not session:
        async with chunked_uploads_lock:
            if session_key in chunked_uploads:
                session = chunked_uploads[session_key]
                received = len(session.get("received_chunks", set()))
                source = "memory"

    if not session:
        return JSONResponse(
            status_code=404,
            content={ # type: ignore
                "status": "not_found",
                "message": "Upload session not found or expired",
                "session_key": session_key,
                "user_id": user_id,
                "upload_id": upload_id,
                "redis_available": redis_client is not None,
                "memory_sessions": list(chunked_uploads.keys())[:5] if chunked_uploads else [],
                "source": source,
            }
        )

    if "total_chunks" not in session:
        raise HTTPException(status_code=404, detail="Invalid upload session") # type: ignore

    total = session["total_chunks"]
    progress = round((received / total * 100), 1) if total > 0 else 0

    return {
        "upload_id": upload_id,
        "filename": session.get("filename"),
        "total_chunks": total,
        "received_chunks": received,
        "progress": progress,
        "created_at": session.get("created_at"),
        "age_seconds": round(time.time() - session.get("created_at", time.time()), 1),
        "status": "completed" if progress >= 100 else "in_progress",
        "source": source,
    }

@app.get("/debug/uploads", tags=["Debug"])
async def list_upload_sessions(user: dict = Depends(enforce_user_access)):
    """Debug endpoint to list active chunked upload sessions for the current user."""
    if user.get("role") != "system":
        raise HTTPException(status_code=403, detail="Admin access required") # type: ignore

    user_id = user.get("user_id")
    sessions = []

    if redis_client:
        cursor = 0
        while True:
            try:
                cursor, keys = await safe_redis_op(
                    redis_client.scan(cursor, match=f"upload_session:{user_id}:*", count=100)
                )
            except Exception as exc:
                logger.warning(f"AARAB | Redis scan failed for upload sessions: {exc}") # type: ignore
                break

            for key in keys or []:
                key_str = key.decode() if isinstance(key, bytes) else key
                try:
                    data = await safe_redis_op(redis_client.get(key_str))
                    if not data:
                        continue # type: ignore
                    session = json.loads(data)
                    chunk_key = key_str.replace("upload_session:", "upload_chunks:")
                    chunks = await safe_redis_op(redis_client.scard(chunk_key), default=0)
                    sessions.append({
                        "upload_id": key_str.split(":")[-1],
                        "filename": session.get("filename"),
                        "total_chunks": session.get("total_chunks"),
                        "received_chunks": chunks or 0,
                        "created_at": session.get("created_at"),
                        "source": "redis",
                    })
                except Exception:
                    pass
 # type: ignore
            if cursor == 0:
                break

    async with chunked_uploads_lock:
        for key, session in chunked_uploads.items():
            if key.startswith(f"{user_id}:"):
                sessions.append({
                    "upload_id": key.split(":")[-1],
                    "filename": session.get("filename"),
                    "total_chunks": session.get("total_chunks"),
                    "received_chunks": len(session.get("received_chunks", set())),
                    "created_at": session.get("created_at"),
                    "source": "memory",
                })

    return {
        "sessions": sessions,
        "count": len(sessions),
        "user_id": user_id,
    }

@app.post("/aarab/process", tags=["AARAB Intelligence"])
async def process_with_aarab_agents(
    request: Request,
    file: Optional[UploadFile] = File(None),
    prompt: Optional[str] = Form(None),
    agent_hint: Optional[str] = Form(None, description="Agent hint (e.g. veritas, chronos, omnis)"),
    auto_route: bool = Form(True),
    conversation_id: Optional[str] = Form(None, alias="workstream_id"),
    workstream_id: Optional[str] = Form(None),
    user: dict = Depends(enforce_user_access),
):
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
 # type: ignore
    # Handle JSON fallback for requests without multipart boundaries (e.g. from PHP gateway)
    if file is None and prompt is None:
        try:
            body = await request.json()
            prompt = body.get("prompt")
            agent_hint = agent_hint or body.get("agent_hint")
            auto_route = body.get("auto_route", auto_route)
            conversation_id = conversation_id or body.get("workstream_id") or body.get("conversation_id") # type: ignore
        except (json.JSONDecodeError, TypeError):
            pass

    if not file and not prompt:
        raise HTTPException(
            status_code=400, 
            detail="Missing required input: Provide a file or a text prompt."
        )

    """ # type: ignore
    Process documents or instructions with the dedicated AARAB v4.0 Intelligence Model Suite.
    
    Available agents:
    - veritas (truth_seeker): Validation & source traceability
    - eclaro (reasoner): Complex causal analysis
    - incepta (ideator): Hypothesis generation
    - clarion (summarizer): Executive briefs
    - aetheris (multimodal_visualizer): Visual outputs
    - chronos (temporal_forecaster): Trend forecasting
    - lumina (viz_architect): Data visualization
    - novara (trend_predictor): Breakthrough scanning
    - vanguarda (breakthrough_monitor): Research monitoring
    - equinox (multimodal_synthesizer): Balanced synthesis
    - voxis (voice_narrator): Audio narration
    - eximio (precision_analyst): High-precision analysis
    - omnis (universal_generalist): Default generalist
    - lumenix (empathy_engine): Tone-tuned responses
    - solara (strategic_foresight): Long-term planning
    - vespera (daily_brief_expert): Daily synthesis
    - sentia (sentiment_analyst): Sentiment analysis
    - spectra (multidimension_analyst): Multi-perspective analysis
    - vigilis (realtime_monitor): Live monitoring
    - astraeon (personalized_ai): Personalized intelligence
    """
    org_id = int(getattr(request.state, "legal_organization_id", 0) or 0)
    if org_id <= 0:
        org_id = int(user.get("legal_organization_id") or user.get("organization_id") or 1) # type: ignore
    
    await enforce_org_document_upload_limit(org_id, request_id)
    
    if file:
        file_path, file_size, hash_sha256 = await save_upload_file_to_tempfile(file)
        filename = file.filename or "document.pdf"
        content_type = file.content_type or "application/pdf"
        validate_upload_file_path(file_path, filename, content_type)
        
        if file_size > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="File too large (max 50MB)")
    else:
        file_path = "internal"
        file_size = len(prompt or "")
        hash_sha256 = hashlib.sha256((prompt or "").encode()).hexdigest()
        filename = "Instruction"
        content_type = "text/plain"
    
    # Load AARAB agents
    await aarab_orchestrator.load_agents() # type: ignore
    
    # Extract text content
    extracted_text = ""
    if file:
        if content_type.startswith("application/pdf") or filename.lower().endswith(".pdf"):
            extracted_text = await asyncio.get_event_loop().run_in_executor(
                None, _extract_pdf_text_safe_from_path, file_path, request_id, 20
            )
        else:
            extracted_text = await asyncio.get_event_loop().run_in_executor(
                None, read_file_head_text, file_path, 10000
            )
    else:
        # No file - use prompt as content
        extracted_text = prompt or ""

    if not extracted_text or not extracted_text.strip():
        raise HTTPException(status_code=400, detail="Could not extract text from document or prompt")
    # Determine which agent to use
    routing_info = None
    resolved_hint = aarab_orchestrator.resolve_agent_type(agent_hint) # type: ignore
    
    if resolved_hint:
        selected_agent = resolved_hint
        logger.info(f"{request_id} | AARAB | Using hinted agent: {selected_agent}")
    elif auto_route:
        routing_info = await aarab_orchestrator.route_document_to_agent_enhanced(
            extracted_text, filename, content_type, prompt
        )
        selected_agent = routing_info['selected_agent']
        logger.info(f"{request_id} | AARAB | Auto-routed to {selected_agent} (confidence: {routing_info['confidence']:.2f})")
    else:
        selected_agent = "universal_generalist"
        logger.info(f"{request_id} | AARAB | Using default agent: Omnis")
    
    # Register document with PHP gateway # type: ignore
    lock_key = f"ingest:aarab:{org_id}:{hash_sha256[:32]}"
    
    async with RedisLock(redis_client, lock_key, ttl=90):
        doc_id = await register_legal_document(
            task_id=int(request.headers.get("X-Task-ID", 0)),
            org_id=org_id,
            filename=filename,
            mime=content_type,
            size=file_size,
            hash_sum=hash_sha256,
            request_id=request_id,
        )
        
        # Queue background indexing with agent attribution
        try:
            routing_payload = json.dumps(routing_info) if routing_info else None
            safe_send_task(
                "process_aarab_document",
                args=[
                    file_path,
                    filename,
                    content_type,
                    org_id,
                    doc_id,
                    request_id,
                    selected_agent,
                    routing_payload,
                ],
            )
        except Exception as e: # type: ignore
            logger.warning(f"{request_id} | AARAB | Queue failed (non-fatal): {e}")
    
    confidence = routing_info['confidence'] if routing_info else (1.0 if resolved_hint else 0.0)
    return await aarab_orchestrator.process_with_agent_with_metrics(
        agent_type=selected_agent,
        content=extracted_text,
        prompt=prompt,
        request=request,
        conversation_id=conversation_id or workstream_id,
        org_id=org_id,
        request_id=request_id,
        filename=filename,
        routing_confidence=confidence,
    )

@app.get("/agents/active", tags=["AARAB Intelligence"])
async def get_active_agents(request: Request):
    """Public agent registry with internal-secret bypass for dynamic updates."""
    internal_secret = request.headers.get("X-Internal-Secret")
    if internal_secret == INTERNAL_SERVICE_SECRET: # type: ignore
        # Return full dynamic list from registry
        await aarab_orchestrator.load_agents()
        return {"agents": await aarab_orchestrator.get_agent_list(), "source": "dynamic"}
    
    # Public endpoint: Return ONLY minimal public info to prevent internal strategy disclosure
    return {
        "agents": [
            {
                "agent_type": agent["agent_type"],
                "name": agent["name"],
                # Exclude internal-only 'optimal_for', 'specialties', 'use_cases'
            }
            for agent in AARABAgentOrchestrator.AGENT_CAPABILITIES.values()
        ],
        "source": "static",
        "total_active": len(AARABAgentOrchestrator.AGENT_CAPABILITIES)
    }

@app.get("/aarab/agents/metrics", tags=["AARAB Intelligence"])
async def get_aarab_agent_metrics(
    request: Request,
    user: dict = Depends(enforce_user_access),
):
    """Get performance metrics for AARAB v4.0 agents"""
    await aarab_orchestrator.load_agents() # type: ignore
    
    agents_info = await aarab_orchestrator.get_agent_list()
    
    return {
        "agents": agents_info,
        "total_agents": len(agents_info),
        "metrics": await aarab_orchestrator.get_agent_metrics(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/aarab/agents/capabilities", tags=["AARAB Intelligence"])
async def get_aarab_agent_capabilities():
    """Static capability map for the AARAB v4.0 agent suite."""
    capabilities = []
    for cap_key, config in AARABAgentOrchestrator.AGENT_CAPABILITIES.items():
        capabilities.append({
            "capability_key": cap_key,
            "agent_type": config["agent_type"],
            "name": config["name"],
            "specialties": config["specialties"],
            "optimal_for": config["optimal_for"],
            "use_cases": [f"Process {focus} documents" for focus in config["optimal_for"][:3]],
        })
    return {
        "suite": "AARAB v4.0 Intelligence Model Suite",
        "total_agents": len(capabilities),
        "agents": capabilities,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/aarab/agents/metrics/enhanced", tags=["AARAB Intelligence"])
async def get_enhanced_agent_metrics(
    request: Request,
    user: dict = Depends(enforce_user_access),
    hours: int = Query(24, ge=1, le=168, description="Hours of historical data"),
    include_trends: bool = Query(True, description="Include hourly trends")
):
    """Enhanced agent metrics with distributed Redis data and trends"""
    await aarab_orchestrator.load_agents() # type: ignore
    agents_info = await aarab_orchestrator.get_agent_list()
    
    metrics, trends = {}, {}
    for agent in agents_info:
        agent_type = agent['agent_type']
        metrics[agent_type] = await distributed_metrics.get_agent_metrics(agent_type)
        if include_trends: trends[agent_type] = await distributed_metrics.get_hourly_trends(agent_type, hours)
    
    org_metrics: Dict[str, Any] = {} # type: ignore
    if user.get('role') in ('system', 'admin'):
        oid = int(getattr(request.state, "legal_organization_id", 0))
        if oid > 0: org_metrics = await distributed_metrics.get_organizational_metrics(oid)
    
    return {
        "agents": agents_info,
        "metrics": metrics,
        "trends": trends if include_trends else None,
        "organizational_metrics": org_metrics,
        "system_health": {
            "average_success_rate": round(sum(m.get('success_rate', 0) for m in metrics.values()) / max(len(metrics), 1), 3),
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    }

@app.post("/documents/analyze", tags=["Legal Infrastructure"], deprecated=True)
async def analyze_document_legacy(
    request: Request,
    file: UploadFile = File(...),
    prompt: Optional[str] = Form(None),
    vertical: str = Form("legal"),
    conversation_id: Optional[str] = Form(None),
):
    """Legacy endpoint for legal analysis. Redirects to /ingest."""
    return await platform_ingest( # type: ignore
        request=request,
        file=file,
        prompt=prompt,
        vertical=vertical,
        conversation_id=conversation_id,
    )

@app.post("/query", tags=["CORE Query"])
async def core_query(
    request: Request,
    body: StreamingChatRequest,
    user: dict = Depends(enforce_user_access),
    _grace: None = Depends(enforce_stream_grace_limits),
):
    """Unified cross-vertical intelligence query endpoint.""" # type: ignore
    return await chat_stream(request, body, user, _grace)


async def _create_single_embedding_internal(o_client: httpx.AsyncClient, text: str) -> Optional[List[float]]:
    """Internal fallback for individual embedding generation."""
    try:
        resp = await o_client.post(
            "/api/embeddings", 
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=httpx.Timeout(45.0)
        )
        return resp.json().get("embedding") # type: ignore
    except Exception as e:
        logger.debug(f"EMBEDDING | Single fallback failed: {e}")
        return None

async def create_batch_embeddings(o_client: httpx.AsyncClient, texts: List[str], org_id: int) -> List[Optional[List[float]]]:
    """Batch embedding generation with robust response handling."""
    if not texts:
        return []
    
    all_results: List[Optional[List[float]]] = [None] * len(texts) # type: ignore
    batch_size = EMBEDDING_BATCH_SIZE
    
    async def process_one_batch(start_idx: int):
        async with embedding_batch_semaphore:
            batch = texts[start_idx:start_idx + batch_size]
            try:
                resp = await o_client.post(
                    "/api/embeddings",
                    json={"model": EMBEDDING_MODEL, "prompt": batch},
                    timeout=httpx.Timeout(60.0)
                )
                resp.raise_for_status()
                data = resp.json()
                
                # Ollama can return either key depending on version/input type # type: ignore
                batch_embeddings = data.get("embeddings") or data.get("embedding")
                
                if isinstance(batch_embeddings, list) and len(batch_embeddings) == len(batch):
                    all_results[start_idx : start_idx + len(batch)] = batch_embeddings
                    embedding_batches_processed.labels(org_id=str(org_id), batch_size=len(batch)).inc()
                    return
                
                logger.warning(f"EMBEDDING | Shape mismatch at {start_idx} (expected {len(batch)})")
            except Exception as e:
                logger.warning(f"EMBEDDING | Batch failed at {start_idx}: {e}")
 # type: ignore
            # Fallback to single embeddings
            for j, text in enumerate(batch):
                all_results[start_idx + j] = await _create_single_embedding_internal(o_client, text)

    # Execute batches
    batch_tasks = [process_one_batch(i) for i in range(0, len(texts), batch_size)]
    await asyncio.gather(*batch_tasks)
    
    return all_results

def _extract_audio_metadata(audio) -> dict:
    """Helper to extract technical metadata from audio objects via mutagen."""
    meta: Dict[str, Any] = {} # type: ignore
    try:
        if audio and hasattr(audio, 'info'):
            meta["duration_seconds"] = getattr(audio.info, 'length', 0)
            meta["bitrate"] = getattr(audio.info, 'bitrate', 0)
            meta["channels"] = getattr(audio.info, 'channels', 0)
            meta["sample_rate"] = getattr(audio.info, 'sample_rate', 0)
    except Exception as e:
        logger.debug(f"Audio metadata extraction failed: {e}")
    return meta

async def upsert_with_retry(client: AsyncQdrantClient, collection_name: str, points: list, max_retries: int = 3):
    """Upsert points to Qdrant with exponential backoff for resilience."""
    for attempt in range(max_retries):
        try:
            return await client.upsert(collection_name=collection_name, points=points, wait=True)
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error(f"QDRANT | Upsert failed after {max_retries} attempts: {e}")
                raise
            wait_time = 2 ** attempt
            logger.warning(f"QDRANT | Upsert attempt {attempt + 1} failed, retrying in {wait_time}s: {e}")
            await asyncio.sleep(wait_time)

async def _index_document_internal(
    file_bytes: bytes, 
    filename: str, 
    content_type: str, 
    org_id: int, 
    doc_id: int, 
    q_client, 
    o_client, 
    r_client=None,
    request_id: str = "background",
    vertical: str = "core"
):
    """ # type: ignore
    Production-grade indexing with:
    - Rich metadata extraction (EXIF, ID3, MP4 tags, etc.)
    - Smart OCR for images & scanned PDFs
    - Audio transcription stub (ready for Whisper/Ollama)
    - Code file language detection
    - Full media support with graceful degradation
    """
    collection_name = get_vector_collection(vertical)
    pages_data: List[tuple[int, str]] = []  # (page_num, text) # type: ignore
    metadata = {
        "filename": filename,
        "content_type": content_type,
        "file_size_bytes": len(file_bytes),
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "extraction_method": "text",
        "has_ocr": False,
        "has_transcription": False,
        "language_detected": None,
        "media_metadata": {}
    }

    file_ext = os.path.splitext(filename)[1].lower()
    logger.info(f"{request_id} | INDEX | Processing {filename} | Size: {len(file_bytes)/ (1024*1024):.2f}MB | Ext: {file_ext}")

    if len(file_bytes) > 50 * 1024 * 1024:  # 50MB hard cap
        logger.warning(f"{request_id} | INDEX | File too large: {len(file_bytes)/(1024*1024):.1f}MB")
        return


    try:
        # ====================== PDF ======================
        if file_ext == '.pdf' or content_type.startswith("application/pdf"):
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            raw_text = ""
            for i, page in enumerate(doc):
                page_text = page.get_text("text")
                pages_data.append((i + 1, page_text))
                raw_text += page_text
            doc.close()

            # Smart OCR fallback
            if len(raw_text.strip()) < 600 or raw_text.count(' ') < 80:
                logger.info(f"{request_id} | INDEX | Low text yield → OCR fallback") # type: ignore
                try:
                    images = convert_from_bytes(file_bytes, dpi=220)
                    ocr_pages = []
                    for i, image in enumerate(images):
                        ocr_text = pytesseract.image_to_string(image, lang='eng+swa')  # Support English + Swahili
                        ocr_pages.append((i + 1, ocr_text))
                    
                    ocr_total = " ".join(t for _, t in ocr_pages).strip()
                    if len(ocr_total) > len(raw_text.strip()) * 1.4:
                        pages_data = ocr_pages
                        metadata["has_ocr"] = True
                        metadata["extraction_method"] = "ocr"
                except Exception as e:
                    logger.warning(f"{request_id} | OCR failed: {e}")

        # ====================== IMAGES ======================
        elif file_ext in IMAGE_EXT:
            try: # type: ignore
                image = Image.open(io.BytesIO(file_bytes))
                metadata["image_size"] = image.size
                metadata["image_format"] = image.format or "unknown"
                
                # EXIF Metadata
                if hasattr(image, '_getexif') and image._getexif():
                    exif = image._getexif()
                    metadata["exif"] = {str(k): str(v) for k, v in exif.items() if v is not None}

                ocr_text = pytesseract.image_to_string(image, lang='eng+swa')
                if ocr_text.strip():
                    pages_data.append((1, ocr_text))
                    metadata["has_ocr"] = True
                    metadata["extraction_method"] = "ocr"
            except Exception as e:
                logger.warning(f"{request_id} | Image processing failed: {e}")
                pages_data.append((1, f"[Image: {filename}]"))

        # ====================== AUDIO ======================
        elif file_ext in AUDIO_EXT:
            metadata["extraction_method"] = "audio" # type: ignore
            try:
                audio = mutagen.File(io.BytesIO(file_bytes))
                if audio:
                    metadata["media_metadata"] = _extract_audio_metadata(audio)  # helper below
            except: pass
            pages_data.append((1, f"[Audio: {filename}] — Use live transcription during recording"))

        # VIDEO
        elif file_ext in VIDEO_EXT:
            metadata["extraction_method"] = "video" # type: ignore
            pages_data.append((1, f"[Video: {filename}] — Keyframes + transcription available via /video/analyze"))

        # ====================== CODE / TEXT FILES ======================
        elif file_ext in CODE_EXT.union({'.txt', '.md', '.json', '.xml', '.yaml', '.yml'}):
            try:
                text = file_bytes.decode("utf-8", errors="ignore")
                pages_data.append((1, text))
                metadata["language_detected"] = "code" if file_ext in CODE_EXT else "natural"
            except:
                pages_data.append((1, file_bytes.decode("latin-1", errors="ignore")))

        # ====================== DOCX / RTF ======================
        elif file_ext == '.docx':
            try: # type: ignore
                doc = Document(io.BytesIO(file_bytes))
                full_text = "\n".join([para.text for para in doc.paragraphs])
                pages_data.append((1, full_text))
            except Exception as e:
                logger.warning(f"Docx failed: {e}")

        elif file_ext == '.rtf':
            try:
                from striprtf.striprtf import rtf_to_text # type: ignore
                text = rtf_to_text(file_bytes.decode("ascii", errors="ignore"))
                pages_data.append((1, text))
            except:
                pass

        if not pages_data:
            logger.warning(f"{request_id} | INDEX | No extractable content for {filename}")
            return

        # ====================== CHUNKING + EMBEDDING ======================
        chunk_manifest: List[dict] = [] # type: ignore
        for page_num, page_text in pages_data:
            if not page_text or not page_text.strip():
                continue
            chunks = chunk_text(page_text)
            for i, chunk in enumerate(chunks):
                if len(chunk.strip()) >= 50:
                    chunk_manifest.append({
                        "text": chunk,
                        "page_number": page_num,
                        "chunk_index": i,
                        "metadata": metadata
                    })

        if not chunk_manifest:
            logger.warning(f"{request_id} | INDEX | No valid chunks for {filename}")
            return

        # ====================== PARALLEL EMBEDDING ======================
        batch_size = 5 # type: ignore
        embedding_tasks = []
        chunk_texts = [c["text"] for c in chunk_manifest]
        for i in range(0, len(chunk_texts), batch_size):
            batch = chunk_texts[i : i + batch_size]
            embedding_tasks.append(create_batch_embeddings(o_client, batch, org_id))

        embedding_results = await asyncio.gather(*embedding_tasks)
        all_embeddings = [emb for batch in embedding_results for emb in batch]

        # Build rich Qdrant points
        points: List[models.PointStruct] = [] # type: ignore
        for i, meta in enumerate(chunk_manifest):
            embedding = all_embeddings[i]
            if embedding and isinstance(embedding, list) and len(embedding) > 100:
                payload = {
                    "text": meta["text"],
                    "doc_id": doc_id,
                    "org_id": org_id,
                    "filename": filename,
                    "page_number": meta["page_number"],
                    "chunk_index": meta["chunk_index"],
                    **meta["metadata"]
                }
                points.append(models.PointStruct(
                    id=str(uuid.uuid4()), # type: ignore
                    vector=embedding,
                    payload=payload
                ))

        if points:
            await upsert_with_retry(q_client, collection_name, points)
            document_chunks_indexed.labels(org_id=str(org_id)).inc(len(points))
            logger.info(f"{request_id} | INDEX | ✅ {len(points)} chunks indexed | {filename} | Metadata enriched")
        else:
            logger.warning(f"{request_id} | INDEX | No embeddings generated for {filename}")

    except Exception as e:
        logger.error(f"{request_id} | INDEX | Critical failure processing {filename}: {e}", exc_info=True)


def chunk_text_legal(
    text: str,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
) -> List[str]:
    chunk_size = chunk_size if chunk_size is not None else LEGAL_CHUNK_SIZE # type: ignore
    chunk_overlap = chunk_overlap if chunk_overlap is not None else LEGAL_CHUNK_OVERLAP
    """
    Advanced legal-aware chunking for Kenyan documents.
    """
    if not text or len(text.strip()) == 0:
        return []

    # Normalize whitespace but preserve paragraph breaks for legal structure
    text = re.sub(r'[ \t]+', ' ', text)  # Normalize horizontal whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)  # Normalize excessive newlines # type: ignore
    
    chunks = []
    start = 0
    
    while start < len(text):
        end = start + chunk_size
        if end >= len(text):
            chunk = text[start:].strip()
            if chunk:
                chunks.append(chunk)
            break
        
        # Strong legal boundaries first
        legal_patterns = [
            r'(?i)\b(Article|Section|Clause|Schedule|Proviso|Definition|Part)\s+\d+[A-Za-z]?\b', # type: ignore
            r'(?i)\b\d+\.\s',           # Numbered paragraphs
            r'\n\s*\n',                 # Paragraph breaks
            r'(?<!\w)\.(?!\d)',         # Sentence end (not decimal)
            r'[;!?]',                   # Strong terminators
        ]
        
        best_break = end
        for pattern in legal_patterns:
            matches = list(re.finditer(pattern, text[start:end + 100])) # type: ignore
            if matches:
                candidate = matches[-1].end() + start
                if candidate > start + 250:  # Avoid tiny chunks
                    best_break = min(candidate, end + 80)
                    break
        
        # Sentence fallback
        if best_break == end:
            for i in range(end, max(start + 200, end - 120), -1): # type: ignore
                if i < len(text) and text[i-1] in '.!?':
                    best_break = i
                    break
        
        chunk = text[start:best_break].strip()
        if chunk and len(chunk) >= 50:
            chunks.append(chunk)
        
        # Overlap
        start = best_break - chunk_overlap
        if start < 0:
            start = 0

    return chunks

def semantic_chunk_text(text: str, threshold: float = 0.45) -> List[str]:
    """
    Hybrid Semantic Chunking.
    Uses SentenceTransformers to find logical breaks where meaning shifts.
    """ # type: ignore
    if SentenceTransformer is None or not text:
        return chunk_text_legal(text)

    # Load a lightweight model for fast local CPU inference
    model = SentenceTransformer(os.getenv("CHUNK_EMBEDDING_MODEL", "all-MiniLM-L6-v2"))
    
    # Split into sentences (simple regex for speed)
    sentences = re.split(r'(?<=[.!?])\s+', text) # type: ignore
    if len(sentences) < 2:
        return [text]

    embeddings = model.encode(sentences)
    
    # Calculate cosine similarity between adjacent sentences
    chunks = []
    current_chunk = [sentences[0]] # type: ignore
    
    for i in range(len(sentences) - 1):
        sim = np.dot(embeddings[i], embeddings[i+1]) / (
            np.linalg.norm(embeddings[i]) * np.linalg.norm(embeddings[i+1])
        )
        
        # If similarity drops below threshold, or chunk gets too large, break
        if sim < threshold or len(" ".join(current_chunk)) > 1200:
            chunks.append(" ".join(current_chunk))
            current_chunk = []
        
        current_chunk.append(sentences[i+1])
    
    if current_chunk:
        chunks.append(" ".join(current_chunk))
        
    return chunks

def chunk_text_simple(text: str, chunk_size: int = 1500, overlap: int = 200) -> List[str]:
    """Simpler chunking for very large documents to prevent memory issues."""
    chunks: List[str] = [] # type: ignore
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks

def chunk_text(text: str) -> List[str]:
    """Primary entry point — Optimized Hybrid Semantic + Legal Chunking"""
    if len(text) > 50000: # type: ignore
        logger.warning(f"CHUNKING | Large document ({len(text)} chars), using simplified chunking")
        return chunk_text_simple(text, chunk_size=1500, overlap=200)
    if len(text) < 2000:
        return chunk_text_legal(text)
    return semantic_chunk_text(text, threshold=0.42)

@app.delete("/chat/history", tags=["Chat History"])
async def clear_all_chat_history(
    request: Request,
    user: dict = Depends(enforce_user_access)
):
    """Clear ALL conversations for the current user."""
    identity_key = request_identity_key(request, user) # type: ignore
    org_id = getattr(request.state, "legal_organization_id", "0")
    legal_organization_id = int(org_id) if str(org_id).isdigit() else 0 # Convert to int

    deleted_count = 0

    # Get keys to delete first (avoid modifying while iterating)
    keys_to_delete = [key for key, data in list(conversation_store.items()) 
                      if key[0] == identity_key and data.get("org_id", 0) == legal_organization_id]

    for ikey, cid in keys_to_delete:
        await delete_conversation(identity_key, cid, legal_organization_id)
        deleted_count += 1

    return {
        "status": "cleared",
        "deleted_count": deleted_count,
        "message": f"Successfully deleted {deleted_count} conversation(s)"
    }

@app.post("/transcribe/stream", tags=["Media"])
async def transcribe_stream(
    request: Request,
    chunk: UploadFile = File(...),
    session_id: str = Form(...),
    user: dict = Depends(enforce_user_access),
):
    """Real-time chunk transcription using Whisper"""
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    audio_bytes = await chunk.read()

    if len(audio_bytes) > 2 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Chunk too large")

    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(
                f"{OLLAMA_HOST}/api/generate",
                json={
                    "model": "whisper",
                    "audio": base64.b64encode(audio_bytes).decode(),
                    "stream": True, # type: ignore
                    "options": {"temperature": 0.0}
                }
            )
            parts = []
            async for line in resp.aiter_lines():
                if line.strip():
                    try:
                        data = json.loads(line)
                        if "response" in data:
                            parts.append(data["response"])
                    except:
                        pass
            transcript = "".join(parts).strip()

        return {
            "success": True, # type: ignore
            "transcript": transcript,
            "session_id": session_id,
            "request_id": request_id
        }
    except Exception as e:
        logger.error(f"{request_id} | STREAM_TRANSCRIBE | {e}")
        return {"success": False, "transcript": "", "error": "Transcription failed"}

@app.post("/video/analyze", tags=["Media"])
async def analyze_video(
    request: Request,
    file: UploadFile = File(...),
    user: dict = Depends(enforce_user_access),
):
    """Video → Keyframes + Whisper Transcription"""
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    file_bytes = await file.read()
    filename = file.filename or "video.webm"

    if len(file_bytes) > 80 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Video too large")

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1]) as tmp:
            tmp.write(file_bytes)
            temp_path = tmp.name

        # 1. Keyframe Extraction # type: ignore
        cap = cv2.VideoCapture(temp_path)
        keyframes = []
        count = 0
        while len(keyframes) < 8 and count < 400:
            ret, frame = cap.read()
            if ret and count % 45 == 0:   # ~every 1.5s at 30fps
                _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                keyframes.append(base64.b64encode(buf.tobytes()).decode())
            count += 1
        cap.release()

        # 2. Audio Extraction + Whisper # type: ignore
        video = VideoFileClip(temp_path)
        audio_path = temp_path + ".wav"
        video.audio.write_audiofile(audio_path, verbose=False, logger=None)

        with open(audio_path, "rb") as f:
            audio_bytes = f.read()

        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(
                f"{OLLAMA_HOST}/api/generate",
                json={"model": "whisper", "audio": base64.b64encode(audio_bytes).decode()}
            )
            transcript = resp.json().get("response", "").strip() # type: ignore

        # Auto-Save Hook: Register the analysis as a permanent intelligence output
        if auth_internal_client:
            asyncio.create_task(auth_internal_client.post(
                "/ai-node/core/internal/register-output",
                json={
                    "task_id": request.headers.get("X-Task-ID"),
                    "organization_id": request.headers.get("X-Organization-ID"),
                    "transcript": transcript,
                    "media_type": "video",
                    "keyframes": keyframes[:6],
                    "duration": round(video.duration, 1)
                },
                headers={
                    "X-Internal-Secret": INTERNAL_SERVICE_SECRET,
                    "X-Internal-Service": INTERNAL_SERVICE_NAME
                }
            ))

        return {
            "success": True, # type: ignore
            "transcript": transcript,
            "keyframes": keyframes[:6],
            "duration": round(video.duration, 1),
            "filename": filename
        }
    finally:
        if temp_path:
            for p in [temp_path, temp_path + ".wav"]:
                if os.path.exists(p):
                    try: os.unlink(p)
                    except: pass

# ============================================================
# DEBUG ENDPOINTS
# ============================================================

@app.get("/debug/whoami", tags=["Debug"])
async def debug_whoami(request: Request, user: dict = Depends(enforce_user_access)):
    """Debug endpoint to verify identity context and verification status."""
    return { # type: ignore
        "user_id": user.get("user_id"),
        "kyc_status": user.get("kyc_status"),
        "is_verified": bool(user.get("is_verified")) or user.get("kyc_status") in ["verified", "approved", "verified_institutional"],
        "role": user.get("role"),
        "org_id": getattr(request.state, "legal_organization_id", None)
    }

@app.get("/debug/routes", tags=["Debug"])
async def debug_routes():
    """List all registered routes to verify /chat exists."""
    routes = []
    for route in app.routes: # type: ignore
        if hasattr(route, "path"):
            routes.append({
                "path": route.path,
                "methods": list(getattr(route, "methods", [])),
                "name": getattr(route, "name", None)
            })
    return {"routes": routes, "timestamp": datetime.now(timezone.utc).isoformat()}

# ============================================================
# ERROR HANDLERS
# ============================================================

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Detailed logger for 422 Unprocessable Entity errors."""
    request_id = getattr(request.state, "request_id", "unknown") # type: ignore
    errors = exc.errors()
    logger.error(f"{request_id} | VALIDATION_ERROR | Path: {request.url.path} | Detail: {errors}")
    return JSONResponse(
        status_code=422,
        content={
            "error": "Validation Failed",
            "detail": errors,
            "request_id": request_id,
            "hint": "Check prompt/messages fields and stream flag"
        }
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Custom HTTP exception handler"""
    response = JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "status_code": exc.status_code,
            "timestamp": datetime.now(timezone.utc).isoformat()
        },
        headers=getattr(exc, "headers", None) # type: ignore
    )
    response.headers["X-Request-ID"] = getattr(request.state, "request_id", "")
    return response

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__": # type: ignore
    port = int(os.getenv("FASTAPI_PORT", 8000))
    host = os.getenv("FASTAPI_HOST", "0.0.0.0")

    logger.info("Starting Arybit AI Node (development mode).")
    logger.info("For production, use Gunicorn + UvicornWorker:")
    logger.info(
        f"   gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app --bind {host}:{port} --log-level info --max-requests 800 --max-requests-jitter 100 --timeout 180 --access-logfile - --error-logfile -"
    )

    uvicorn.run(
        "main:app",
        host=host, port=port, reload=False, log_level="info", workers=1
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=app.config['PORT'], debug=app.config['DEBUG'])
