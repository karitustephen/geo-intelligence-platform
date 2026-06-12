"""Lightweight Google Embeddings - Mock version for development without heavy imports"""

import os
import logging
import hashlib
import random
from typing import List, Optional, Dict, Any

logger = logging.getLogger(__name__)

# Configuration from environment
DISABLE_HEAVY_IMPORTS = os.environ.get('DISABLE_HEAVY_IMPORTS', 'false').lower() == 'true'
DISABLE_EMBEDDINGS = os.environ.get('DISABLE_EMBEDDINGS', 'false').lower() == 'true'
USE_MOCK = DISABLE_HEAVY_IMPORTS or DISABLE_EMBEDDINGS


class LightweightEmbeddingsClient:
    """Lightweight embeddings client that doesn't require heavy ML libraries"""
    
    def __init__(self):
        self._initialized = False
        self.model = "mock"
        self._cache: Dict[str, List[float]] = {}
    
    def initialize(self, model: str = "text-embedding-004"):
        """Initialize the client - always succeeds in mock mode"""
        self._initialized = True
        self.model = model
        if USE_MOCK:
            logger.info(f"✅ Google Embeddings initialized in MOCK mode with model: {model}")
        else:
            logger.info(f"✅ Google Embeddings lightweight mode with model: {model}")
    
    @property
    def is_ready(self) -> bool:
        return self._initialized
    
    def get_embedding_dimension(self, model: Optional[str] = None) -> int:
        """Return standard embedding dimension"""
        return 384
    
    def _get_deterministic_embedding(self, text: str) -> List[float]:
        """Generate deterministic but random-looking embedding"""
        seed = hashlib.md5(text.encode()).hexdigest()
        random.seed(int(seed[:8], 16))
        embedding = [random.uniform(-0.3, 0.3) for _ in range(384)]
        norm = sum(x * x for x in embedding) ** 0.5
        if norm > 0:
            embedding = [x / norm for x in embedding]
        random.seed()
        return embedding
    
    async def create_embedding(
        self,
        text: str,
        model: Optional[str] = None,
        task_type: Optional[str] = "RETRIEVAL_DOCUMENT",
        truncate: bool = True
    ) -> Optional[List[float]]:
        """Generate embedding - returns deterministic mock embeddings"""
        if not self._initialized:
            logger.warning("Embeddings client not initialized")
            return None
        if not text or not text.strip():
            return None
        cache_key = hashlib.md5(f"{model or self.model}:{task_type}:{text}".encode()).hexdigest()
        if cache_key in self._cache:
            return self._cache[cache_key]
        embedding = self._get_deterministic_embedding(text)
        self._cache[cache_key] = embedding
        if len(self._cache) > 10000:
            keys_to_remove = list(self._cache.keys())[:2000]
            for key in keys_to_remove:
                del self._cache[key]
        return embedding
    
    async def create_batch_embeddings(
        self,
        texts: List[str],
        model: Optional[str] = None,
        task_type: Optional[str] = "RETRIEVAL_DOCUMENT",
        batch_size: int = 10
    ) -> List[Optional[List[float]]]:
        """Generate embeddings for multiple texts"""
        if not texts:
            return []
        results: List[Optional[List[float]]] = []
        for text in texts:
            results.append(await self.create_embedding(text, model, task_type))
        return results
    
    async def create_query_embedding(self, query: str, model: Optional[str] = None) -> Optional[List[float]]:
        """Create query embedding"""
        return await self.create_embedding(query, model, task_type="RETRIEVAL_QUERY")
    
    async def create_document_embedding(self, document: str, model: Optional[str] = None) -> Optional[List[float]]:
        """Create document embedding"""
        return await self.create_embedding(document, model, task_type="RETRIEVAL_DOCUMENT")
    
    async def create_similarity_embedding(self, text: str, model: Optional[str] = None) -> Optional[List[float]]:
        """Create similarity embedding"""
        return await self.create_embedding(text, model, task_type="SEMANTIC_SIMILARITY")
    
    async def get_embedding_statistics(self) -> Dict[str, Any]:
        """Get statistics"""
        return {
            "initialized": self._initialized,
            "model": self.model,
            "mode": "mock" if USE_MOCK else "lightweight",
            "cache_size": len(self._cache),
            "dimension": self.get_embedding_dimension()
        }
    
    def clear_cache(self):
        """Clear the cache"""
        self._cache.clear()
        logger.info("Embedding cache cleared")
    
    async def close(self):
        """Clean up"""
        self._initialized = False
        self._cache.clear()
        logger.info("Embeddings client closed")


# Singleton instance - this will always work
google_embeddings = LightweightEmbeddingsClient()
