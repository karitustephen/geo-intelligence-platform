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
REPO_NAME="geo-intelligence"
IMAGE_NAME="${REGION}-docker.pkg.dev/$PROJECT_ID/$REPO_NAME/$SERVICE_NAME:latest"
RUN_SA_EMAIL="arybit-cloudrun-sa@$PROJECT_ID.iam.gserviceaccount.com"

# Ensure Artifact Registry repository exists
gcloud artifacts repositories create "$REPO_NAME" --repository-format=docker --location="$REGION" --quiet 2>/dev/null || true

# Build Docker image
echo "Building Docker image..."
cd "$GEE_API_DIR"
docker build -t "$IMAGE_NAME" .

# Push to Artifact Registry
echo "Pushing to Artifact Registry..."
docker push "$IMAGE_NAME"

# Deploy to Cloud Run
echo "Deploying to Cloud Run..."
gcloud run deploy "$SERVICE_NAME" \
    --image "$IMAGE_NAME" \
    --platform managed \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --memory 4Gi \
    --cpu 2 \
    --min-instances 1 \
    --max-instances 20 \
    --concurrency 80 \
    --timeout 300 \
    --service-account "$RUN_SA_EMAIL" \
    --set-env-vars="ENVIRONMENT=production,LOG_LEVEL=INFO,PORT=8080" \
    --set-secrets="GEMINI_API_KEY=gee-api-gemini-key:latest,JWT_SECRET=gee-api-jwt-secret:latest" \
    --allow-unauthenticated=false \
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
echo "Running authenticated health check..."
ID_TOKEN=$(gcloud auth print-identity-token)
curl -sf -H "Authorization: Bearer $ID_TOKEN" "$SERVICE_URL/health" >/dev/null \
    && echo "✅ Service is healthy" || echo "⚠️  Health check failed (Status code: $(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $ID_TOKEN" "$SERVICE_URL/health"))"