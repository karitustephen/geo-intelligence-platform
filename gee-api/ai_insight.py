"""
AI insight generation using Google Gemini AI
"""

import json
from typing import Dict, Any, Optional, AsyncGenerator
from datetime import datetime

from config import get_config
from utils.exceptions import GeminiError


class AIInsightGenerator:
    """Generate environmental insights using Google Gemini AI"""
    
    def __init__(self):
        self.config = get_config()
        self._client = None
    
    def _init_client(self):
        """Initialize Gemini AI client"""
        if self._client:
            return
        
        try:
            from google import genai
            self._client = genai.Client(api_key=self.config.gemini.api_key)
        except Exception as e:
            raise GeminiError(f"Failed to initialize client: {str(e)}")
    
    async def generate_analysis(self, prompt: str, stream: bool = False) -> Dict[str, Any]:
        """Generate AI analysis from prompt"""
        self._init_client()
        
        try:
            if stream:
                return await self._stream_response(prompt)
            else:
                return await self._generate_response(prompt)
        except Exception as e:
            raise GeminiError(str(e))
    
    async def _generate_response(self, prompt: str) -> Dict[str, Any]:
        """Generate non-streaming response"""
        response = self._client.models.generate_content(
            model=self.config.gemini.model,
            contents=prompt,
            config={
                "temperature": self.config.gemini.temperature,
                "max_output_tokens": self.config.gemini.max_output_tokens
            }
        )
        
        return {
            "content": response.text,
            "model": self.config.gemini.model,
            "usage": {
                "prompt_tokens": getattr(response, 'usage_metadata', {}).get('prompt_token_count', 0),
                "completion_tokens": getattr(response, 'usage_metadata', {}).get('candidates_token_count', 0)
            }
        }
    
    async def _stream_response(self, prompt: str):
        """Generate streaming response"""
        response = self._client.models.generate_content_stream(
            model=self.config.gemini.model,
            contents=prompt,
            config={
                "temperature": self.config.gemini.temperature,
                "max_output_tokens": self.config.gemini.max_output_tokens
n            }
        )
        
        async def stream():
            for chunk in response:
                if chunk.text:
                    yield chunk.text
        
        return stream()
    
    async def analyze_vegetation(self, ndvi_data: Dict, location: Dict) -> Dict[str, Any]:
        """Analyze vegetation health from NDVI data"""
        self._init_client()
        
        prompt = f"""
        Analyze vegetation health based on NDVI data:
        
        Location: {json.dumps(location)}
        NDVI Values: {json.dumps(ndvi_data)}
        
        Provide:
        1. Current vegetation health assessment
        2. Trend analysis (improving, stable, degrading)
        3. Potential causes for observed patterns
        4. Actionable recommendations
        """
        
        return await self._generate_response(prompt)
    
    async def assess_change(self, change_data: Dict, region: Dict) -> Dict[str, Any]:
        """Assess environmental change significance"""
        self._init_client()
        
        prompt = f"""
        Assess environmental change significance:
        
        Region: {json.dumps(region)}
        Change Detection Results: {json.dumps(change_data)}
        
        Provide:
        1. Assessment of change severity
        2. Potential environmental impacts
        3. Recommended monitoring frequency
        4. Suggested interventions if needed
        """
        
        return await self._generate_response(prompt)
    
    async def generate_report(self, analyses: Dict, format: str = "summary") -> Dict[str, Any]:
        """Generate comprehensive environmental report"""
        self._init_client()
        
        prompt = f"""
        Generate an environmental report based on the following analyses:
        
        {json.dumps(analyses, indent=2)}
        
        Report Format: {format}
        
        Include:
        1. Executive summary
        2. Key findings
        3. Risk assessment
        4. Recommendations
        5. Data limitations
        """
        
        return await self._generate_response(prompt)
