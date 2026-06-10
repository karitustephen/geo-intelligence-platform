"""
Async Gemini AI client
"""

import asyncio
import logging
from typing import Optional, Dict, Any, AsyncGenerator

from config import get_config
from utils.exceptions import GeminiError

try:
    from google import genai
    from google.genai.types import GenerateContentConfig
    GOOGLE_AI_AVAILABLE = True
except ImportError:
    GOOGLE_AI_AVAILABLE = False
    logger = logging.getLogger(__name__)
    logger.warning("Google Generative AI library not available")

logger = logging.getLogger(__name__)


class AsyncGeminiClient:
    """Async wrapper for Gemini AI"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def initialize(self):
        """Initialize Gemini client"""
        if self._initialized:
            return
        
        if not GOOGLE_AI_AVAILABLE:
            raise GeminiError("Google Generative AI library not available")
        
        config = get_config()
        if not config.gemini.api_key:
            raise GeminiError("GEMINI_API_KEY not configured")
        
        try:
            self.client = genai.Client(api_key=config.gemini.api_key)
            self._initialized = True
            logger.info(f"Gemini AI initialized with model: {config.gemini.model}")
        except Exception as e:
            logger.error(f"Gemini initialization failed: {e}")
            raise GeminiError(f"Gemini initialization failed: {e}")
    
    @property
    def is_ready(self) -> bool:
        return self._initialized and GOOGLE_AI_AVAILABLE
    
    async def generate(self, prompt: str, stream: bool = False):
        """Generate text with Gemini"""
        if not self.is_ready:
            raise GeminiError("Gemini AI service not ready")
        
        config = get_config()
        gen_config = GenerateContentConfig(
            temperature=config.gemini.temperature,
            max_output_tokens=config.gemini.max_output_tokens,
            top_p=config.gemini.top_p,
            top_k=config.gemini.top_k
        )
        
        if stream:
            return self._stream_generate(prompt, gen_config)
        
        try:
            loop = asyncio.get_running_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self.client.models.generate_content(model=config.gemini.model, contents=prompt, config=gen_config)
            )
            return getattr(response, 'text', str(response))
        except Exception as e:
            raise GeminiError(str(e))
    
    async def _stream_generate(self, prompt: str, gen_config):
        config = get_config()
        response = self.client.models.generate_content_stream(model=config.gemini.model, contents=prompt, config=gen_config)
        for chunk in response:
            if getattr(chunk, 'text', None): yield chunk.text

gemini = AsyncGeminiClient()