#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# GCP AUTHENTICATION - Service account or user auth
# ============================================================

# Check if running in Cloud Run / GCE
if [[ -n "${K_SERVICE:-}" ]] || [[ -n "${GCE_METADATA_HOST:-}" ]]; then
    echo "Detected Google Cloud environment - using metadata service"
    export GOOGLE_APPLICATION_CREDENTIALS=""
    
    # Get project ID from metadata if not set
    if [[ -z "${GOOGLE_CLOUD_PROJECT:-}" ]]; then
        export GOOGLE_CLOUD_PROJECT=$(curl -s -H "Metadata-Flavor: Google" \
            http://metadata.google.internal/computeMetadata/v1/project/project-id)
        echo "Project ID from metadata: $GOOGLE_CLOUD_PROJECT"
    fi
else
    # Local development - use service account or ADC
    if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ]] && [[ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]]; then
        echo "Using service account: $GOOGLE_APPLICATION_CREDENTIALS"
        # Test credentials
        gcloud auth activate-service-account --key-file="$GOOGLE_APPLICATION_CREDENTIALS"
    else
        echo "Using Application Default Credentials"
        gcloud auth application-default login --quiet 2&gt;/dev/null || true
    fi
fi

# Set project
if [[ -n "${GOOGLE_CLOUD_PROJECT:-}" ]]; then
    gcloud config set project "$GOOGLE_CLOUD_PROJECT" 2&gt;/dev/null || true
    echo "Project set to: $GOOGLE_CLOUD_PROJECT"
fi

# Enable required APIs
echo "Enabling required GCP APIs..."
gcloud services enable \
    cloudresourcemanager.googleapis.com \
    compute.googleapis.com \
    run.googleapis.com \
    secretmanager.googleapis.com \
    --quiet 2&gt;/dev/null || true

echo "✅ GCP authentication complete"