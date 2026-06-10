"""
Async Earth Engine client with thread pool execution
"""

import asyncio
import logging
import ee
from typing import Any, Dict, Optional
from functools import partial

from config import get_config
from utils.exceptions import EarthEngineError

logger = logging.getLogger(__name__)


class AsyncEarthEngine:
    """Async wrapper for Earth Engine operations"""
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def initialize(self):
        """Initialize Earth Engine (runs once)"""
        if self._initialized:
            return
        
        config = get_config()
        try:
            credentials = config.get_gee_credentials() if hasattr(config, 'get_gee_credentials') else None
            if credentials:
                ee.Initialize(credentials, project=config.earth_engine.project_id)
            elif config.earth_engine.project_id:
                ee.Initialize(project=config.earth_engine.project_id)
            else:
                ee.Initialize()

            self._initialized = True
            logger.info("Google Earth Engine initialized")
        except Exception as e:
            logger.error(f"Earth Engine initialization failed: {e}")
            raise EarthEngineError(str(e), "initialize")
    
    @property
    def is_ready(self) -> bool:
        return self._initialized
    
    async def execute(self, func, *args, **kwargs) -> Any:
        """Execute Earth Engine operation in thread pool"""
        if not self._initialized:
            self.initialize()

        loop = asyncio.get_running_loop()
        
        try:
            result = await loop.run_in_executor(
                None,
                partial(func, *args, **kwargs)
            )
            return result
        except Exception as e:
            logger.error(f"Earth Engine execution failed: {e}")
            raise EarthEngineError(str(e), "execution")
    
    async def get_info(self, ee_object) -> Any:
        """Async wrapper for ee_object.getInfo()"""
        return await self.execute(lambda: ee_object.getInfo())
    
    async def get_ndvi(self, point, buffer_meters: int = 100, date: str = None) -> Optional[float]:
        """Calculate NDVI at a point"""
        def _calculate():
            collection = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
            if date:
                collection = collection.filterDate(date, date)
            
            image = collection.first()
            if not image: return None
            
            ndvi = image.normalizedDifference(['B8', 'B4']).rename('NDVI')
            return ndvi.reduceRegion(reducer=ee.Reducer.mean(), geometry=point.buffer(buffer_meters), scale=10).get('NDVI').getInfo()
        
        return await self.execute(_calculate)

gee = AsyncEarthEngine()