#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# CLOUD RUN DEPLOYMENT - Production deployment
# ============================================================

echo "☁️ Deploying to Cloud Run"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEE_API_DIR="$SCRIPT_DIR/../gee-api"

# Configuration
SERVICE_NAME="${SERVICE_NAME:-geo-intelligence-api}"
REGION="${REGION:-us-central1}"
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-}"
IMAGE_NAME="gcr.io/$PROJECT_ID/$SERVICE_NAME:latest"

# Build Docker image
echo "Building Docker image..."
cd "$GEE_API_DIR"
docker build -t "$IMAGE_NAME" --build-arg APP_MODULE=main:app .

# Push to Container Registry
echo "Pushing to Container Registry..."
docker push "$IMAGE_NAME"

# Deploy to Cloud Run
echo "Deploying to Cloud Run..."
gcloud run deploy "$SERVICE_NAME" \
    --image "$IMAGE_NAME" \
    --platform managed \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --memory 2Gi \
    --cpu 2 \
    --concurrency 80 \
    --timeout 300 \
    --min-instances 1 \
    --max-instances 10 \
    --allow-unauthenticated \
    --set-env-vars="ENVIRONMENT=production,LOG_LEVEL=INFO,APP_MODULE=main:app" \
    --set-secrets="GEMINI_API_KEY=gee-api-gemini-key:latest,JWT_SECRET=gee-api-jwt-secret:latest" \
    --quiet

# Get the URL
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
    --platform managed \
    --region "$REGION" \
    --format="value(status.url)")

echo "✅ Deployment complete!"
echo "📍 Service URL: $SERVICE_URL"
echo "📍 Health Check: $SERVICE_URL/health"

# Run health check against deployed service
echo "Running health check..."
curl -sf "$SERVICE_URL/health" >/dev/null && echo "✅ Service is healthy" || echo "⚠️  Health check failed"