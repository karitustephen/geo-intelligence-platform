"""
AI insight routes using Gemini for the geospatial intelligence API
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from middleware.auth_middleware import require_verified_user
from models import (
    EnvironmentalInsightRequest,
    VegetationHealthRequest,
    WildfireRiskRequest,
    ChangeInterpretationRequest,
    ForecastRequest
)
from services.insight_service import InsightService
from utils.response import success_response, error_response

router = APIRouter(prefix="/api/insights", tags=["AI Insights"])
insight_service = InsightService()


@router.post("/environmental")
async def get_environmental_insights(
    request: EnvironmentalInsightRequest,
    user: dict = Depends(require_verified_user)
):
    """
    Get AI-powered environmental insights using Google Gemini AI
    
    Analyzes vegetation health, water bodies, and provides recommendations
    """
    try:
        result = await insight_service.get_environmental_insights(request)
        return success_response(
            data=result,
            metadata={
                "user_id": user.get("user_id"),
                "model": insight_service.gemini_model
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        return error_response(
            error=str(e),
            error_code="INSIGHT_GENERATION_FAILED",
            status_code=500
        )


@router.post("/vegetation-health")
async def analyze_vegetation_health(
    request: VegetationHealthRequest,
    user: dict = Depends(require_verified_user)
):
    """
    Comprehensive vegetation health analysis with AI interpretation
    
    Returns NDVI, EVI, NDMI values along with AI-generated insights
    """
    try:
        result = await insight_service.analyze_vegetation_health(request)
        return success_response(
            data=result,
            metadata={
                "metrics": request.metrics,
                "user_id": user.get("user_id")
            }
        )
    except Exception as e:
        return error_response(
            error=str(e),
            error_code="VEGETATION_ANALYSIS_FAILED",
            status_code=500
        )


@router.post("/wildfire-risk")
async def assess_wildfire_risk(
    request: WildfireRiskRequest,
    user: dict = Depends(require_verified_user)
):
    """
    AI-powered wildfire risk assessment using vegetation moisture data
    
    Returns risk level (low/medium/high/critical) and recommendations
    """
    try:
        result = await insight_service.assess_wildfire_risk(request)
        return success_response(
            data=result,
            metadata={
                "assessment_date": request.date,
                "user_id": user.get("user_id")
            }
        )
    except Exception as e:
        return error_response(
            error=str(e),
            error_code="RISK_ASSESSMENT_FAILED",
            status_code=500
        )


@router.post("/change-interpretation")
async def interpret_change(
    request: ChangeInterpretationRequest,
    user: dict = Depends(require_verified_user)
):
    """
    AI-powered interpretation of change detection results
    
    Provides natural language explanation of detected changes
    """
    try:
        result = await insight_service.interpret_change(request)
        return success_response(
            data=result,
            metadata={"user_id": user.get("user_id")}
        )
    except Exception as e:
        return error_response(
            error=str(e),
            error_code="CHANGE_INTERPRETATION_FAILED",
            status_code=500
        )


@router.post("/forecast")
async def generate_forecast(
    request: ForecastRequest,
    user: dict = Depends(require_verified_user)
):
    """
    Generate environmental forecast with AI insights
    
    Combines time series forecasting with Gemini interpretation
    """
    try:
        result = await insight_service.generate_forecast(request)
        return success_response(
            data=result,
            metadata={
                "forecast_days": request.forecast_days,
                "user_id": user.get("user_id")
            }
        )
    except Exception as e:
        return error_response(
            error=str(e),
            error_code="FORECAST_FAILED",
            status_code=500
        )