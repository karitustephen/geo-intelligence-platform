"""
Google Gemini Embeddings Client
"""

import asyncio
import hashlib
import logging
import time
from typing import List, Optional, Dict, Any
from collections import OrderedDict
import threading

from config import get_config

logger = logging.getLogger(__name__)

try:
    from google import genai
    from google.genai.types import EmbedContentConfig
    GOOGLE_AI_AVAILABLE = True
except ImportError:
    GOOGLE_AI_AVAILABLE = False

class BoundedEmbeddingCache:
    def __init__(self, maxsize: int = 10000, ttl_seconds: int = 86400):
        self.cache = OrderedDict()
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self.timestamps = {}
        self.lock = threading.RLock()
    
    def get(self, key: str) -> Optional[List[float]]:
        with self.lock:
            if key in self.cache:
                if time.time() - self.timestamps.get(key, 0) > self.ttl_seconds:
                    self.cache.pop(key, None)
                    return None
                self.cache.move_to_end(key)
                return self.cache[key]
            return None

    def set(self, key: str, value: List[float]):
        with self.lock:
            if len(self.cache) >= self.maxsize: self.cache.popitem(last=False)
            self.cache[key] = value
            self.timestamps[key] = time.time()

class GoogleEmbeddingsClient:
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None: cls._instance = super().__new__(cls)
        return cls._instance
    
    def initialize(self):
        if self._initialized: return
        config = get_config()
        self.client = genai.Client(api_key=config.gemini.api_key)
        self.model = config.google_embedding_model
        self.cache = BoundedEmbeddingCache(maxsize=config.embedding_cache_max_size, ttl_seconds=config.embedding_cache_ttl)
        self._initialized = True

    async def create_embedding(self, text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> Optional[List[float]]:
        if not self._initialized: self.initialize()
        cache_key = hashlib.sha256(f"{self.model}:{task_type}:{text}".encode()).hexdigest()
        cached = self.cache.get(cache_key)
        if cached: return cached
        
        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None, 
                lambda: self.client.models.embed_content(model=self.model, contents=text[:8000], config=EmbedContentConfig(task_type=task_type))
            )
            emb = response.embeddings[0].values if response.embeddings else None
            if emb: self.cache.set(cache_key, emb)
            return emb
        except Exception as e:
            logger.error(f"Embedding failed: {e}")
            return None

google_embeddings = GoogleEmbeddingsClient()