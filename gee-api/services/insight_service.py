"""
AI insight service using Google Gemini AI
"""

import json
import time
from typing import Dict, Any, List, Optional, AsyncGenerator

from config import get_config
from utils.exceptions import GeminiError


class InsightService:
    """Service for AI-powered environmental insights using Gemini"""
    
    def __init__(self):
        self.config = get_config()
        self._gemini_initialized = False
        self.gemini_model = self.config.gemini.model
    
    def _init_gemini(self):
        """Initialize Gemini AI client"""
        if self._gemini_initialized:
            return
        
        try:
            from google import genai
            from google.genai.types import GenerateContentConfig
            
            self.client = genai.Client(api_key=self.config.gemini.api_key)
            self._gemini_initialized = True
        except Exception as e:
            raise GeminiError(f"Failed to initialize: {str(e)}")
    
    async def get_environmental_insights(self, request) -> Dict[str, Any]:
        """Generate AI-powered environmental insights"""
        self._init_gemini()
        
        prompt = self._build_insight_prompt(request)
        
        try:
            response = self.client.models.generate_content(
                model=self.gemini_model,
                contents=prompt,
                config={
                    "temperature": self.config.gemini.temperature,
                    "max_output_tokens": self.config.gemini.max_output_tokens
                }
            )
            
            return {
                "insights": response.text,
                "query": request.query,
                "model": self.gemini_model,
                "generated_at": time.time()
            }
        except Exception as e:
            raise GeminiError(str(e))
    
    def _build_insight_prompt(self, request) -> str:
        """Build prompt for Gemini AI"""
        system_prompt = """You are Arybit Geospatial Intelligence, an expert environmental monitoring AI.
Provide accurate, data-driven analysis using satellite imagery. Be concise and actionable."""
        
        prompt = system_prompt + "\n\n"
        
        if request.context_data:
            prompt += f"## Geospatial Data:\n{json.dumps(request.context_data, indent=2)}\n\n"
        
        prompt += f"## User Query:\n{request.query}\n\n"
        prompt += "## Response Requirements:\n"
        prompt += "1. Assess the current environmental situation\n"
        prompt += "2. Identify trends or changes\n"
        prompt += "3. Provide actionable recommendations\n"
        prompt += "4. Note any data limitations\n"
        
        return prompt
    
    async def analyze_vegetation_health(self, request) -> Dict[str, Any]:
        """Analyze vegetation health with AI interpretation"""
        self._init_gemini()
        
        # Build analysis prompt
        prompt = f"""
        Analyze vegetation health for location: {request.location.dict()}
        Time range: {request.time_range.start_date} to {request.time_range.end_date}
        Metrics: {', '.join(request.metrics)}
        
        Provide:
        1. Overall vegetation health assessment
        2. Trend analysis (improving, stable, degrading)
        3. Potential causes for observed patterns
        4. Actionable recommendations for land management
        """
        
        try:
            response = self.client.models.generate_content(
                model=self.gemini_model,
                contents=prompt,
                config={"temperature": self.config.gemini.temperature}
            )
            
            return {
                "analysis": response.text,
                "metrics": request.metrics,
                "location": request.location.dict(),
                "model": self.gemini_model
            }
        except Exception as e:
            raise GeminiError(str(e))
    
    async def assess_wildfire_risk(self, request) -> Dict[str, Any]:
        """Assess wildfire risk with AI"""
        self._init_gemini()
        
        prompt = f"""
        Assess wildfire risk for region: {request.region.dict()}
        Assessment date: {request.date}
        
        Based on vegetation moisture data, provide:
        1. Risk level (Low/Medium/High/Critical)
        2. Primary contributing factors
        3. Specific recommendations for:
           - Monitoring frequency
           - Prevention measures
           - Response preparedness
        """
        
        try:
            response = self.client.models.generate_content(
                model=self.gemini_model,
                contents=prompt,
                config={"temperature": self.config.gemini.temperature}
            )
            
            return {
                "risk_assessment": response.text,
                "region": request.region.dict(),
                "date": request.date,
                "model": self.gemini_model
            }
        except Exception as e:
            raise GeminiError(str(e))
    
    async def interpret_change(self, request) -> Dict[str, Any]:
        """Interpret change detection results"""
        self._init_gemini()
        
        prompt = f"""
        Interpret these environmental change detection results:
        
        Change Data: {json.dumps(request.change_data, indent=2)}
        Region: {json.dumps(request.region.dict())}
        Time Period: {request.time_range.start_date} to {request.time_range.end_date}
        
        Provide:
        1. Summary of significant changes detected
        2. Environmental implications
        3. Potential drivers of change
        4. Recommended follow-up actions
        """
        
        try:
            response = self.client.models.generate_content(
                model=self.gemini_model,
                contents=prompt,
                config={"temperature": self.config.gemini.temperature}
            )
            
            return {
                "interpretation": response.text,
                "change_data": request.change_data,
                "model": self.gemini_model
            }
        except Exception as e:
            raise GeminiError(str(e))
    
    async def generate_forecast(self, request) -> Dict[str, Any]:
        """Generate environmental forecast with AI"""
        self._init_gemini()
        
        prompt = f"""
        Generate environmental forecast for location: {request.location.dict()}
        
        Historical data (last {request.historical_days} days):
        {json.dumps(request.historical_data, indent=2)}
        
        Forecast horizon: {request.forecast_days} days
        
        Provide:
        1. Predicted trends for key environmental metrics
        2. Expected changes and their significance
        3. Recommendations for adaptation or mitigation
        4. Confidence level for the forecast
        """
        
        try:
            response = self.client.models.generate_content(
                model=self.gemini_model,
                contents=prompt,
                config={
                    "temperature": self.config.gemini.temperature,
                    "max_output_tokens": self.config.gemini.max_output_tokens
                }
            )
            
            return {
                "forecast": response.text,
                "location": request.location.dict(),
                "forecast_days": request.forecast_days,
                "model": self.gemini_model
            }
        except Exception as e:
            raise GeminiError(str(e))