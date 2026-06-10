"""
Core module for GEE API - Earth Engine, Gemini, Embeddings clients
"""

from .gee_client import AsyncEarthEngine, gee
from .gemini_client import AsyncGeminiClient, gemini
from .embeddings_client import GoogleEmbeddingsClient, google_embeddings

__all__ = [
    'AsyncEarthEngine',
    'gee',
    'AsyncGeminiClient', 
    'gemini',
    'GoogleEmbeddingsClient',
    'google_embeddings'
]