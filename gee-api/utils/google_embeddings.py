"""
Google Gemini Embeddings Client - Production Grade
Uses Google's embedding models for high-quality vector representations
"""

import asyncio
import hashlib
import logging
import time
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import get_config

logger = logging.getLogger(__name__)

# Try to import Google AI libraries
try:
    from google import genai
    try:
        from google.genai.types import EmbedContentConfig, TaskType
    except ImportError:
        from google.genai import types
        EmbedContentConfig = types.EmbedContentConfig
        TaskType = getattr(types, 'TaskType', None)
    GOOGLE_AI_AVAILABLE = True
except Exception as e:
    GOOGLE_AI_AVAILABLE = False
    logger.warning(f"Google Generative AI library not available: {e}. Install: pip install google-genai")

# Try to import sentence-transformers as fallback
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except Exception:
    SENTENCE_TRANSFORMERS_AVAILABLE = False


class CircuitBreaker:
    """Circuit breaker for external service calls"""
    
    def __init__(self, name: str, threshold: int = 5, timeout: int = 30):
        self.name = name
        self.threshold = threshold
        self.timeout = timeout
        self.failures = 0
        self.last_failure = 0.0
        self.lock = asyncio.Lock()
    
    async def call(self, func, *args, **kwargs):
        async with self.lock:
            if self.failures >= self.threshold:
                elapsed = time.time() - self.last_failure
                if elapsed < self.timeout:
                    logger.warning(f"Circuit breaker '{self.name}' is OPEN (failures={self.failures})")
                    raise Exception(f"Service '{self.name}' temporarily unavailable")
                logger.info(f"Circuit breaker '{self.name}' half-open - allowing probe")
                self.failures = self.threshold - 1
        
        try:
            result = await func(*args, **kwargs)
            await self.record_success()
            return result
        except Exception as e:
            await self.record_failure()
            raise
    
    async def record_success(self):
        async with self.lock:
            if self.failures > 0:
                logger.info(f"Circuit breaker '{self.name}' CLOSED after success")
            self.failures = 0
    
    async def record_failure(self):
        async with self.lock:
            self.failures += 1
            self.last_failure = time.time()
            logger.warning(f"Circuit breaker '{self.name}' failure {self.failures}/{self.threshold}")


class BoundedEmbeddingCache:
    """Bounded LRU cache for embeddings with TTL"""
    
    def __init__(self, maxsize: int = 10000, ttl_seconds: int = 3600):
        from collections import OrderedDict
        import threading
        
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
                    self.timestamps.pop(key, None)
                    return None
                self.cache.move_to_end(key)
                return self.cache[key]
            return None
    
    def set(self, key: str, value: List[float]):
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            else:
                if len(self.cache) >= self.maxsize:
                    oldest = next(iter(self.cache))
                    self.cache.pop(oldest)
                    self.timestamps.pop(oldest, None)
                self.cache[key] = value
            self.timestamps[key] = time.time()
    
    def clear_expired(self):
        with self.lock:
            now = time.time()
            expired = [k for k, ts in self.timestamps.items() if now - ts > self.ttl_seconds]
            for k in expired:
                self.cache.pop(k, None)
                self.timestamps.pop(k, None)


class GoogleEmbeddingsClient:
    """
    Google Gemini Embeddings Client
    Supports multiple embedding models with automatic fallback
    """
    
    _instance = None
    _initialized = False
    
    # Available embedding models
    EMBEDDING_MODELS = {
        "gemini-embedding-exp-03-07": {
            "dimensions": 768,
            "description": "Experimental embedding model (March 2025)",
            "max_input_tokens": 2048,
            "task_types": ["RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT", "SEMANTIC_SIMILARITY", "CLASSIFICATION"]
        },
        "text-embedding-004": {
            "dimensions": 768,
            "description": "Latest text embedding model",
            "max_input_tokens": 2048,
            "task_types": ["RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT", "SEMANTIC_SIMILARITY", "CLASSIFICATION"]
        },
        "text-embedding-3-small": {
            "dimensions": 1536,
            "description": "High-performance embedding model",
            "max_input_tokens": 8192,
            "task_types": ["RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT", "SEMANTIC_SIMILARITY"]
        },
        "text-embedding-3-large": {
            "dimensions": 3072,
            "description": "Highest quality embeddings",
            "max_input_tokens": 8192,
            "task_types": ["RETRIEVAL_QUERY", "RETRIEVAL_DOCUMENT", "SEMANTIC_SIMILARITY"]
        }
    }
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def initialize(self, model: str = "text-embedding-004"):
        """Initialize the Google Gemini client"""
        if self._initialized:
            return
        
        if not GOOGLE_AI_AVAILABLE:
            logger.error("Google Generative AI library not available")
            raise RuntimeError("Google Generative AI library required for embeddings")
        
        config = get_config()
        api_key = getattr(config, 'gemini', None)
        # Support both config patterns: config.gemini.api_key or settings.GEMINI_API_KEY
        gemini_api_key = None
        try:
            gemini_api_key = config.gemini.api_key
        except Exception:
            gemini_api_key = os.getenv('GEMINI_API_KEY')

        if not gemini_api_key:
            logger.error("GEMINI_API_KEY not configured")
            raise RuntimeError("GEMINI_API_KEY is required for Google embeddings")
        
        try:
            self.client = genai.Client(api_key=gemini_api_key)
            self.model = model
            self._initialized = True
            
            # Initialize cache
            self.cache = BoundedEmbeddingCache(
                maxsize=getattr(config, 'embedding_cache_max_size', 10000),
                ttl_seconds=getattr(config, 'embedding_cache_ttl', 86400)
            )
            
            # Initialize circuit breaker
            self.circuit_breaker = CircuitBreaker(
                name="google_embeddings",
                threshold=getattr(config, 'circuit_breaker_threshold', 5),
                timeout=getattr(config, 'circuit_breaker_timeout', 30)
            )
            
            # Thread pool for batch operations
            self.executor = ThreadPoolExecutor(max_workers=getattr(config, 'cpu_executor_threads', 4))
            
            logger.info(f"Google Embeddings client initialized with model: {model}")
            logger.info(f"Available models: {list(self.EMBEDDING_MODELS.keys())}")
            
        except Exception as e:
            logger.error(f"Google Embeddings initialization failed: {e}")
            raise RuntimeError(f"Google Embeddings initialization failed: {e}")
    
    @property
    def is_ready(self) -> bool:
        return self._initialized and GOOGLE_AI_AVAILABLE
    
    def get_embedding_dimension(self, model: Optional[str] = None) -> int:
        """Get the embedding dimension for a model"""
        model_name = model or self.model
        return self.EMBEDDING_MODELS.get(model_name, {}).get("dimensions", 768)
    
    def _get_cache_key(self, text: str, model: str, task_type: Optional[str] = None) -> str:
        """Generate cache key for text embedding"""
        content = f"{model}:{task_type}:{text}"
        return hashlib.sha256(content.encode()).hexdigest()
    
    async def create_embedding(
        self,
        text: str,
        model: Optional[str] = None,
        task_type: Optional[str] = "RETRIEVAL_DOCUMENT",
        truncate: bool = True
    ) -> Optional[List[float]]:
        """
        Generate embedding for a single text using Google Gemini
        """
        if not self.is_ready:
            logger.error("Google Embeddings client not ready")
            return None
        
        if not text or not text.strip():
            logger.warning("Empty text provided for embedding")
            return None
        
        # Truncate if needed
        max_tokens = self.EMBEDDING_MODELS.get(model or self.model, {}).get("max_input_tokens", 2048)
        text = self._truncate_text(text, max_tokens) if truncate else text
        
        # Check cache
        cache_key = self._get_cache_key(text, model or self.model, task_type)
        cached = self.cache.get(cache_key)
        if cached:
            logger.debug(f"Embedding cache hit for key: {cache_key[:16]}")
            return cached
        
        async def _embed():
            try:
                # Use thread pool for synchronous API call
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    self.executor,
                    lambda: self.client.models.embed_content(
                        model=model or self.model,
                        contents=text,
                        config=EmbedContentConfig(
                            task_type=task_type,
                            output_dimensionality=self.EMBEDDING_MODELS.get(model or self.model, {}).get("dimensions")
                        )
                    )
                )
                
                # Extract embedding
                embedding = response.embeddings[0].values if response.embeddings else None
                
                if embedding:
                    # Cache the result
                    self.cache.set(cache_key, embedding)
                    logger.debug(f"Generated embedding for text length: {len(text)} chars")
                    return embedding
                else:
                    logger.warning("No embedding returned from API")
                    return None
                    
            except Exception as e:
                logger.error(f"Google embedding generation failed: {e}")
                raise
        
        try:
            return await self.circuit_breaker.call(_embed)
        except Exception as e:
            logger.error(f"Circuit breaker prevented embedding: {e}")
            # Fallback to sentence-transformers if available
            return await self._fallback_embedding(text)
    
    async def create_batch_embeddings(
        self,
        texts: List[str],
        model: Optional[str] = None,
        task_type: Optional[str] = "RETRIEVAL_DOCUMENT",
        batch_size: int = 10
    ) -> List[Optional[List[float]]]:
        """
        Generate embeddings for multiple texts in batch
        """
        if not self.is_ready:
            logger.error("Google Embeddings client not ready")
            return [None] * len(texts)
        
        if not texts:
            return []
        
        results = [None] * len(texts)
        actual_batch_size = min(batch_size, getattr(get_config(), 'google_embedding_batch_size', 10))
        
        # Process in batches
        for i in range(0, len(texts), actual_batch_size):
            batch = texts[i:i + actual_batch_size]
            batch_indices = list(range(i, min(i + actual_batch_size, len(texts))))
            
            # Check cache for each text
            uncached_indices = []
            uncached_texts = []
            
            for idx, text in zip(batch_indices, batch):
                cache_key = self._get_cache_key(text, model or self.model, task_type)
                cached = self.cache.get(cache_key)
                if cached:
                    results[idx] = cached
                else:
                    uncached_indices.append(idx)
                    uncached_texts.append(text)
            
            # Generate embeddings for uncached texts
            if uncached_texts:
                batch_embeddings = await self._batch_embed(uncached_texts, model, task_type)
                
                for idx, embedding in zip(uncached_indices, batch_embeddings):
                    if embedding:
                        results[idx] = embedding
                        # Cache the result
                        cache_key = self._get_cache_key(texts[idx], model or self.model, task_type)
                        self.cache.set(cache_key, embedding)
        
        return results
    
    async def _batch_embed(
        self,
        texts: List[str],
        model: Optional[str] = None,
        task_type: Optional[str] = "RETRIEVAL_DOCUMENT"
    ) -> List[Optional[List[float]]]:
        """Internal method for batch embedding"""
        
        async def _batch_request():
            try:
                # Prepare batch request
                loop = asyncio.get_event_loop()
                
                # For batch requests, we can either:
                # 1. Use multiple parallel single requests (simpler)
                # 2. Use the batch API if available (more efficient)
                
                # Using parallel single requests for reliability
                tasks = [
                    self.create_embedding(text, model, task_type)
                    for text in texts
                ]
                
                return await asyncio.gather(*tasks, return_exceptions=True)
                
            except Exception as e:
                logger.error(f"Batch embedding failed: {e}")
                return [None] * len(texts)
        
        try:
            results = await self.circuit_breaker.call(_batch_request)
            # Convert exceptions to None
            return [r if not isinstance(r, Exception) else None for r in results]
        except Exception as e:
            logger.error(f"Circuit breaker prevented batch embedding: {e}")
            return [None] * len(texts)
    
    async def create_query_embedding(self, query: str, model: Optional[str] = None) -> Optional[List[float]]:
        """Create embedding optimized for query/retrieval tasks"""
        return await self.create_embedding(
            text=query,
            model=model,
            task_type="RETRIEVAL_QUERY"
        )
    
    async def create_document_embedding(self, document: str, model: Optional[str] = None) -> Optional[List[float]]:
        """Create embedding optimized for document storage"""
        return await self.create_embedding(
            text=document,
            model=model,
            task_type="RETRIEVAL_DOCUMENT"
        )
    
    async def create_similarity_embedding(self, text: str, model: Optional[str] = None) -> Optional[List[float]]:
        """Create embedding for semantic similarity tasks"""
        return await self.create_embedding(
            text=text,
            model=model,
            task_type="SEMANTIC_SIMILARITY"
        )
    
    async def _fallback_embedding(self, text: str) -> Optional[List[float]]:
        """Fallback to sentence-transformers when Google API fails"""
        if not SENTENCE_TRANSFORMERS_AVAILABLE:
            logger.warning("No embedding fallback available")
            return None
        
        try:
            loop = asyncio.get_event_loop()
            model = SentenceTransformer('all-MiniLM-L6-v2')
            embedding = await loop.run_in_executor(self.executor, model.encode, text)
            return embedding.tolist()
        except Exception as e:
            logger.error(f"Fallback embedding failed: {e}")
            return None
    
    def _truncate_text(self, text: str, max_tokens: int = 2048) -> str:
        """Truncate text to approximate token limit"""
        # Rough estimate: 4 chars per token
        max_chars = max_tokens * 4
        if len(text) > max_chars:
            logger.debug(f"Truncating text from {len(text)} to {max_chars} chars")
            return text[:max_chars]
        return text
    
    async def get_embedding_statistics(self) -> Dict[str, Any]:
        """Get statistics about the embedding cache and service"""
        return {
            "initialized": self._initialized,
            "model": self.model,
            "available_models": list(self.EMBEDDING_MODELS.keys()),
            "cache_size": len(self.cache.cache) if hasattr(self, 'cache') else 0,
            "cache_maxsize": self.cache.maxsize if hasattr(self, 'cache') else 0,
            "dimension": self.get_embedding_dimension(),
            "google_ai_available": GOOGLE_AI_AVAILABLE,
            "sentence_transformers_available": SENTENCE_TRANSFORMERS_AVAILABLE
        }
    
    def clear_cache(self):
        """Clear the embedding cache"""
        if hasattr(self, 'cache'):
            self.cache.cache.clear()
            self.cache.timestamps.clear()
            logger.info("Embedding cache cleared")
    
    async def close(self):
        """Clean up resources"""
        if hasattr(self, 'executor'):
            self.executor.shutdown(wait=True)
        logger.info("Google Embeddings client closed")


# Singleton instance
google_embeddings = GoogleEmbeddingsClient()
