"""
BigQuery pipeline for geospatial analytics
"""

import json
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from config import get_config
from utils.exceptions import StorageError


class BigQueryPipeline:
    """BigQuery integration for geospatial data analytics"""
    
    def __init__(self):
        self.config = get_config()
        self._client = None
    
    def _init_client(self):
        """Initialize BigQuery client"""
        if self._client:
            return
        
        try:
            from google.cloud import bigquery
            self._client = bigquery.Client(project=self.config.storage.gcp_project_id)
        except Exception as e:
            raise StorageError(str(e), "bigquery")
    
    async def store_analysis(self, analysis_id: str, user_id: str, analysis_type: str, result: Dict) -> str:
        """Store analysis result in BigQuery"""
        self._init_client()
        
        table_id = f"{self.config.storage.gcp_project_id}.{self.config.storage.bigquery_dataset}.analyses"
        
        rows = [{
            "analysis_id": analysis_id,
            "user_id": user_id,
            "analysis_type": analysis_type,
            "result_json": json.dumps(result),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "completed"
        }]
        
        errors = self._client.insert_rows_json(table_id, rows)
        if errors:
            raise StorageError(f"Failed to store analysis: {errors}", "bigquery")
        
        return analysis_id
    
    async def get_historical_trends(
        self,
        location: Dict,
        metric: str,
        start_date: str,
        end_date: str
    ) -> List[Dict]:
        """Query historical trends from BigQuery"""
        self._init_client()
        
        query = f"""
        SELECT 
            DATE(timestamp) as date,
            AVG(CAST(JSON_EXTRACT(result_json, '$.{metric}') AS FLOAT64)) as avg_value,
            COUNT(*) as sample_count
        FROM `{self.config.storage.gcp_project_id}.{self.config.storage.bigquery_dataset}.analyses`
        WHERE 
            JSON_EXTRACT(result_json, '$.location') = '{json.dumps(location)}'
            AND timestamp >= '{start_date}'
            AND timestamp <= '{end_date}'
        GROUP BY DATE(timestamp)
        ORDER BY date ASC
        """
        
        try:
            results = self._client.query(query).result()
            return [
                {
                    "date": row.date.isoformat(),
                    "value": row.avg_value,
                    "sample_count": row.sample_count
                }
                for row in results
            ]
        except Exception as e:
            raise StorageError(str(e), "bigquery")
    
    async def get_user_statistics(self, user_id: str) -> Dict:
        """Get user analysis statistics"""
        self._init_client()
        
        query = f"""
        SELECT 
            COUNT(*) as total_analyses,
            COUNT(DISTINCT analysis_type) as unique_types,
            MIN(created_at) as first_analysis,
            MAX(created_at) as last_analysis
        FROM `{self.config.storage.gcp_project_id}.{self.config.storage.bigquery_dataset}.analyses`
        WHERE user_id = '{user_id}'
        """
        
        try:
            results = self._client.query(query).result()
            row = list(results)[0]
            return {
                "total_analyses": row.total_analyses,
                "unique_types": row.unique_types,
                "first_analysis": row.first_analysis.isoformat() if row.first_analysis else None,
                "last_analysis": row.last_analysis.isoformat() if row.last_analysis else None
            }
        except Exception as e:
            raise StorageError(str(e), "bigquery")
    
    async def aggregate_by_region(
        self,
        region: Dict,
        analysis_type: str,
        start_date: str,
        end_date: str
    ) -> List[Dict]:
        """Aggregate analyses by region"""
        self._init_client()
        
        query = f"""
        SELECT 
            JSON_EXTRACT(result_json, '$.location') as location,
            AVG(CAST(JSON_EXTRACT(result_json, '$.value') AS FLOAT64)) as avg_value,
            COUNT(*) as analysis_count
        FROM `{self.config.storage.gcp_project_id}.{self.config.storage.bigquery_dataset}.analyses`
        WHERE 
            analysis_type = '{analysis_type}'
            AND timestamp >= '{start_date}'
            AND timestamp <= '{end_date}'
        GROUP BY location
        """
        
        try:
            results = self._client.query(query).result()
            return [
                {
                    "location": json.loads(row.location) if row.location else None,
                    "avg_value": row.avg_value,
                    "analysis_count": row.analysis_count
                }
                for row in results
            ]
        except Exception as e:
            raise StorageError(str(e), "bigquery")
