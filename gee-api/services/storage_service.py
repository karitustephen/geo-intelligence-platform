"""
Storage service for geospatial data using Google Cloud Storage and BigQuery
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from config import get_config
from utils.exceptions import StorageError, NotFoundError


class StorageService:
    """Service for storing and retrieving geospatial analysis results"""
    
    def __init__(self):
        self.config = get_config()
        self._gcs_client = None
        self._bq_client = None
    
    def _init_gcs(self):
        """Initialize Google Cloud Storage client"""
        if self._gcs_client:
            return
        
        try:
            from google.cloud import storage
            self._gcs_client = storage.Client(project=self.config.storage.gcp_project_id)
        except Exception as e:
            raise StorageError(str(e), self.config.storage.bucket_name)
    
    def _init_bigquery(self):
        """Initialize BigQuery client"""
        if self._bq_client:
            return
        
        try:
            from google.cloud import bigquery
            self._bq_client = bigquery.Client(project=self.config.storage.gcp_project_id)
        except Exception as e:
            raise StorageError(str(e), "bigquery")
    
    async def upload_geotiff(self, file, user_id: str) -> Dict[str, Any]:
        """Upload GeoTIFF file to Cloud Storage"""
        self._init_gcs()
        
        try:
            # Generate unique filename
            file_id = str(uuid.uuid4())
            extension = file.filename.split('.')[-1] if '.' in file.filename else 'tif'
            blob_name = f"uploads/{user_id}/{file_id}.{extension}"
            
            # Upload to GCS
            bucket = self._gcs_client.bucket(self.config.storage.bucket_name)
            blob = bucket.blob(blob_name)
            
            content = await file.read()
            blob.upload_from_string(content, content_type=file.content_type)
            
            # Store metadata in BigQuery
            await self._store_upload_metadata(file_id, user_id, blob_name, file.filename)
            
            return {
                "file_id": file_id,
                "filename": file.filename,
                "bucket": self.config.storage.bucket_name,
                "path": blob_name,
                "size_bytes": len(content),
                "uploaded_at": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            raise StorageError(str(e), self.config.storage.bucket_name)
    
    async def _store_upload_metadata(self, file_id: str, user_id: str, path: str, filename: str):
        """Store upload metadata in BigQuery"""
        self._init_bigquery()
        
        table_id = f"{self.config.storage.gcp_project_id}.{self.config.storage.bigquery_dataset}.file_metadata"
        
        rows = [{
            "file_id": file_id,
            "user_id": user_id,
            "filename": filename,
            "gcs_path": path,
            "uploaded_at": datetime.now(timezone.utc).isoformat(),
            "status": "uploaded"
        }]
        
        errors = self._bq_client.insert_rows_json(table_id, rows)
        if errors:
            raise StorageError(f"Failed to store metadata: {errors}", "bigquery")
    
    async def get_analysis(self, analysis_id: str, user_id: str) -> Optional[Dict]:
        """Retrieve analysis result by ID"""
        self._init_bigquery()
        
        query = f"""
        SELECT *
        FROM `{self.config.storage.gcp_project_id}.{self.config.storage.bigquery_dataset}.analyses`
        WHERE analysis_id = '{analysis_id}' AND user_id = '{user_id}'
        """
        
        try:
            result = self._bq_client.query(query).result()
            rows = list(result)
            if not rows:
                return None
            
            row = rows[0]
            return {
                "analysis_id": row.analysis_id,
                "type": row.analysis_type,
                "result": json.loads(row.result_json),
                "created_at": row.created_at.isoformat(),
                "status": row.status
            }
        except Exception as e:
            raise StorageError(str(e), "bigquery")
    
    async def list_user_analyses(self, user_id: str, limit: int = 50, offset: int = 0) -> List[Dict]:
        """List all analyses for a user"""
        self._init_bigquery()
        
        query = f"""
        SELECT analysis_id, analysis_type, created_at, status
        FROM `{self.config.storage.gcp_project_id}.{self.config.storage.bigquery_dataset}.analyses`
        WHERE user_id = '{user_id}'
        ORDER BY created_at DESC
        LIMIT {limit} OFFSET {offset}
        """
        
        try:
            results = self._bq_client.query(query).result()
            return [
                {
                    "analysis_id": row.analysis_id,
                    "type": row.analysis_type,
                    "created_at": row.created_at.isoformat(),
                    "status": row.status
                }
                for row in results
            ]
        except Exception as e:
            raise StorageError(str(e), "bigquery")
    
    async def delete_analysis(self, analysis_id: str, user_id: str):
        """Delete analysis result"""
        self._init_bigquery()
        
        query = f"""
        DELETE FROM `{self.config.storage.gcp_project_id}.{self.config.storage.bigquery_dataset}.analyses`
        WHERE analysis_id = '{analysis_id}' AND user_id = '{user_id}'
        """
        
        try:
            self._bq_client.query(query).result()
        except Exception as e:
            raise StorageError(str(e), "bigquery")
    
    async def export_analysis(self, analysis_id: str, format: str, user_id: str) -> Dict:
        """Export analysis in specified format"""
        analysis = await self.get_analysis(analysis_id, user_id)
        if not analysis:
            raise NotFoundError("Analysis", analysis_id)
        
        if format == "geojson":
            return self._to_geojson(analysis)
        elif format == "csv":
            return self._to_csv(analysis)
        else:
            return {"export_url": f"/exports/{analysis_id}.{format}"}
    
    def _to_geojson(self, analysis: Dict) -> Dict:
        """Convert analysis to GeoJSON format"""
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "analysis_id": analysis["analysis_id"],
                        "type": analysis["type"],
                        "result": analysis["result"]
                    },
                    "geometry": analysis["result"].get("geometry", {})
                }
            ]
        }
    
    def _to_csv(self, analysis: Dict) -> Dict:
        """Convert analysis to CSV format"""
        return {
            "csv_url": f"/exports/{analysis['analysis_id']}.csv",
            "rows": len(analysis.get("result", {}).get("data", []))
        }