"""
Storage service for geospatial data using Google Cloud Storage and BigQuery
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Union

import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from google.cloud import bigquery
from config import get_config
from utils.exceptions import StorageError, NotFoundError

logger = structlog.get_logger(__name__)


class StorageService:
    """Service for storing and retrieving geospatial analysis results"""

    _ALLOWED_CONTENT_TYPES = {
        "image/tiff",
        "image/geotiff",
        "image/x-tiff",
        "application/tiff",
        "application/x-tiff",
    }
    _MAX_UPLOAD_SIZE = 1024 * 1024 * 500  # 500 MB
    _ANALYSIS_STATUSES = [
        "uploaded",
        "queued",
        "processing",
        "completed",
        "failed",
        "archived",
    ]

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

        content_type = (file.content_type or "").lower()
        if content_type not in self._ALLOWED_CONTENT_TYPES:
            logger.warning("invalid_file_type", content_type=content_type, filename=file.filename, user_id=user_id)
            raise StorageError("Invalid file type for GeoTIFF upload", "INVALID_FILE_TYPE", 400)

        if not hasattr(file.file, "seek") or not hasattr(file.file, "tell"):
            raise StorageError("Uploaded file must be seekable for validation", "INVALID_FILE_STREAM", 400)

        file.file.seek(0, 2)
        file_size = file.file.tell()
        file.file.seek(0)

        if file_size > self._MAX_UPLOAD_SIZE:
            logger.warning("file_too_large", file_size=file_size, max_size=self._MAX_UPLOAD_SIZE, filename=file.filename, user_id=user_id)
            raise StorageError(
                f"File exceeds maximum upload size of {self._MAX_UPLOAD_SIZE // (1024 * 1024)} MB",
                "FILE_TOO_LARGE",
                413
            )

        try:
            file_id = str(uuid.uuid4())
            extension = file.filename.split('.')[-1] if '.' in file.filename else 'tif'
            blob_name = f"uploads/{user_id}/{file_id}.{extension}"

            bucket = self._gcs_client.bucket(self.config.storage.bucket_name)
            blob = bucket.blob(blob_name)

            await asyncio.to_thread(blob.upload_from_file, file.file, content_type=content_type, rewind=True)

            await self._store_upload_metadata(file_id, user_id, blob_name, file.filename)

            logger.info(
                "upload_complete",
                file_id=file_id,
                user_id=user_id,
                bucket=self.config.storage.bucket_name,
                path=blob_name,
                file_size=file_size,
            )

            return {
                "file_id": file_id,
                "filename": file.filename,
                "bucket": self.config.storage.bucket_name,
                "path": blob_name,
                "uploaded_at": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            logger.error("upload_failed", error=str(e), user_id=user_id, filename=file.filename)
            raise StorageError(str(e), self.config.storage.bucket_name)
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
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

        errors = await asyncio.to_thread(self._bq_client.insert_rows_json, table_id, rows)
        if errors:
            raise StorageError(f"Failed to store metadata: {errors}", "bigquery")

    async def save_analysis(
        self,
        analysis_id: str,
        user_id: str,
        analysis_type: str,
        result: Any,
        status: str = "uploaded",
        created_at: Optional[datetime] = None,
    ) -> None:
        """Save an analysis record to BigQuery"""
        self._init_bigquery()

        if status not in self._ANALYSIS_STATUSES:
            raise StorageError(f"Invalid analysis status '{status}'", "INVALID_STATUS", 400)

        table_id = f"{self.config.storage.gcp_project_id}.{self.config.storage.bigquery_dataset}.analyses"
        row = {
            "analysis_id": analysis_id,
            "user_id": user_id,
            "analysis_type": analysis_type,
            "result_json": result,
            "created_at": (created_at or datetime.now(timezone.utc)).isoformat(),
            "status": status,
        }

        errors = await asyncio.to_thread(self._bq_client.insert_rows_json, table_id, [row])
        if errors:
            raise StorageError(f"Failed to save analysis record: {errors}", "bigquery")

    async def _execute_bq_query(self, query: str, job_config: bigquery.QueryJobConfig):
        """Run a BigQuery query with retries"""
        self._init_bigquery()
        return await asyncio.to_thread(self._bq_client.query, query, job_config=job_config)

    def _parse_json_field(self, value: Any) -> Any:
        """Safely normalize JSON fields from BigQuery results"""
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    async def generate_download_url(self, path: str, expiration: int = 3600) -> str:
        """Generate a signed URL for a GCS object"""
        self._init_gcs()

        if not path:
            raise StorageError("GCS path is required to generate a signed URL", "MISSING_GCS_PATH", 400)

        bucket = self._gcs_client.bucket(self.config.storage.bucket_name)
        blob = bucket.blob(path)

        try:
            url = await asyncio.to_thread(
                blob.generate_signed_url,
                version="v4",
                expiration=timedelta(seconds=expiration),
                method="GET",
                response_type="application/octet-stream",
            )
            return url
        except Exception as e:
            raise StorageError(str(e), "gcs")

    async def get_analysis(self, analysis_id: str, user_id: str) -> Optional[Dict]:
        """Retrieve analysis result by ID"""
        self._init_bigquery()

        query = """
        SELECT analysis_id, analysis_type, result_json, created_at, status
        FROM `{project}.{dataset}.analyses`
        WHERE analysis_id = @analysis_id AND user_id = @user_id
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("analysis_id", "STRING", analysis_id),
                bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            ]
        )

        try:
            formatted_query = query.format(
                project=self.config.storage.gcp_project_id,
                dataset=self.config.storage.bigquery_dataset
            )
            query_job = await self._execute_bq_query(formatted_query, job_config=job_config)
            result = await asyncio.to_thread(query_job.result)
            rows = list(result)
            if not rows:
                return None

            row = rows[0]
            result_json = self._parse_json_field(row.result_json)
            return {
                "analysis_id": row.analysis_id,
                "type": row.analysis_type,
                "result": result_json,
                "created_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
                "status": row.status
            }
        except Exception as e:
            raise StorageError(str(e), "bigquery")
    
    async def list_user_analyses(self, user_id: str, limit: int = 50, offset: int = 0) -> List[Dict]:
        """List all analyses for a user"""
        self._init_bigquery()
        
        query = """
        SELECT analysis_id, analysis_type, created_at, status
        FROM `{self.config.storage.gcp_project_id}.{self.config.storage.bigquery_dataset}.analyses`
        WHERE user_id = @user_id
        ORDER BY created_at DESC
        LIMIT @limit OFFSET @offset
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
                bigquery.ScalarQueryParameter("offset", "INT64", offset),
            ]
        )
        
        try:
            query_job = await asyncio.to_thread(self._bq_client.query, query.format(self=self), job_config=job_config)
            results = await asyncio.to_thread(query_job.result)
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
        
        query = """
        DELETE FROM `{self.config.storage.gcp_project_id}.{self.config.storage.bigquery_dataset}.analyses`
        WHERE analysis_id = @analysis_id AND user_id = @user_id
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("analysis_id", "STRING", analysis_id),
                bigquery.ScalarQueryParameter("user_id", "STRING", user_id),
            ]
        )
        
        try:
            query_job = await asyncio.to_thread(self._bq_client.query, query.format(self=self), job_config=job_config)
            await asyncio.to_thread(query_job.result)
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
        elif format == "signed_url":
            if not analysis.get("result") or not isinstance(analysis["result"], dict):
                raise StorageError("Cannot generate signed URL for analysis without valid GCS path", "INVALID_ANALYSIS_RESULT", 400)
            return {"download_url": await self.generate_download_url(analysis["result"].get("gcs_path", ""))}
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
                    "geometry": (analysis["result"] or {}).get("geometry", {})
                }
            ]
        }
    
    def _to_csv(self, analysis: Dict) -> Dict:
        """Convert analysis to CSV format"""
        return {
            "csv_url": f"/exports/{analysis['analysis_id']}.csv",
            "rows": len(analysis.get("result", {}).get("data", []))
        }