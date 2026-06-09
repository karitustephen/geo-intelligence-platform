from google.cloud import bigquery

class BigQueryPipeline:
    def __init__(self):
        self.client = bigquery.Client()
