import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    APP_NAME = os.getenv("APP_NAME", "geo-intelligence-platform")
    APP_ENV = os.getenv("APP_ENV", "production")
    GCP_PROJECT = os.getenv("GCP_PROJECT")
    BIGQUERY_DATASET = os.getenv("BIGQUERY_DATASET", "gee_dataset")
    DEBUG = os.getenv("DEBUG", "False").lower() == "true"
    PORT = int(os.getenv("PORT", 8080))
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-key-for-now")