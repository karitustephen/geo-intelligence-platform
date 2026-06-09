"""
Core Earth Engine functionality for the geospatial intelligence API
"""

import ee
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta

from config import get_config
from utils.exceptions import EarthEngineError


class GEECore:
    """Core Earth Engine operations"""
    
    def __init__(self):
        self.config = get_config()
        self._initialized = False
    
    def initialize(self):
        """Initialize Earth Engine"""
        if self._initialized:
            return
        
        try:
            if self.config.earth_engine.private_key_json:
                credentials = ee.ServiceAccountCredentials(
                    self.config.earth_engine.service_account,
                    key_data=json.dumps(self.config.earth_engine.private_key_json)
                )
            elif self.config.earth_engine.private_key_path:
                credentials = ee.ServiceAccountCredentials(
                    self.config.earth_engine.service_account,
                    key_file=self.config.earth_engine.private_key_path
                )
            else:
                credentials = None
            
            if credentials:
                ee.Initialize(credentials, project=self.config.earth_engine.project_id)
            else:
                ee.Initialize(project=self.config.earth_engine.project_id)
            
            self._initialized = True
        except Exception as e:
            raise EarthEngineError(str(e), "initialize")
    
    def get_sentinel_collection(self, start_date: str, end_date: str, max_cloud: int = 20) -> ee.ImageCollection:
        """Get Sentinel-2 image collection"""
        return (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                .filterDate(start_date, end_date)
                .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', max_cloud)))
    
    def get_landsat_collection(self, start_date: str, end_date: str, max_cloud: int = 20) -> ee.ImageCollection:
        """Get Landsat 8/9 image collection"""
        return (ee.ImageCollection('LANDSAT/LC08/C02/T1_L2')
                .filterDate(start_date, end_date)
                .filter(ee.Filter.lt('CLOUD_COVER', max_cloud)))
    
    def calculate_ndvi(self, image: ee.Image, sensor: str = "sentinel") -> ee.Image:
        """Calculate NDVI based on sensor type"""
        if sensor == "sentinel":
            ndvi = image.normalizedDifference(['B8', 'B4'])
        else:
            ndvi = image.normalizedDifference(['B5', 'B4'])
        return ndvi.rename('NDVI')
    
    def calculate_evi(self, image: ee.Image) -> ee.Image:
        """Calculate Enhanced Vegetation Index"""
        nir = image.select('B8')
        red = image.select('B4')
        blue = image.select('B2')
        evi = nir.subtract(red).multiply(2.5).divide(
            nir.add(red.multiply(6)).subtract(blue.multiply(7.5)).add(1)
        )
        return evi.rename('EVI')
    
    def calculate_ndwi(self, image: ee.Image) -> ee.Image:
        """Calculate Normalized Difference Water Index"""
        ndwi = image.normalizedDifference(['B3', 'B8'])
        return ndwi.rename('NDWI')
    
    def calculate_ndmi(self, image: ee.Image) -> ee.Image:
        """Calculate Normalized Difference Moisture Index"""
        ndmi = image.normalizedDifference(['B8', 'B11'])
        return ndmi.rename('NDMI')
    
    def calculate_msavi2(self, image: ee.Image) -> ee.Image:
        """Calculate Modified Soil Adjusted Vegetation Index 2"""
        nir = image.select('B8')
        red = image.select('B4')
        msavi2 = nir.add(1).subtract(
            (nir.add(1).pow(2).subtract(red.multiply(8))).sqrt()
        ).divide(2)
        return msavi2.rename('MSAVI2')
    
    def get_time_series(self, point: ee.Geometry, collection: ee.ImageCollection, scale: int = 10) -> List[Dict]:
        """Extract time series values at a point"""
        values = []
        size = collection.size().getInfo()
        
        for i in range(min(size, 100)):
            image = ee.Image(collection.toList(size).get(i))
            date = image.date().format().getInfo()
            
            value = image.reduceRegion(
                reducer=ee.Reducer.mean(),
                geometry=point,
                scale=scale,
                bestEffort=True
            ).getInfo()
            
            if value:
                values.append({
                    "date": date[:10],
                    "value": value
                })
        
        return values
    
    def export_to_cloud_storage(
        self,
        image: ee.Image,
        description: str,
        bucket: str,
        filename: str,
        region: ee.Geometry,
        scale: int = 10
    ) -> str:
        """Export image to Google Cloud Storage"""
        task = ee.batch.Export.image.toCloudStorage(
            image=image,
            description=description,
            bucket=bucket,
            fileNamePrefix=filename,
            region=region,
            scale=scale,
            maxPixels=1e9
        )
        task.start()
        return task.status()['id']
