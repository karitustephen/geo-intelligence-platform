#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# SECRETS SETUP - Google Cloud Secret Manager
# ============================================================

echo "🔐 Setting up secrets in Google Cloud Secret Manager"

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-}"
if [[ -z "$PROJECT_ID" ]]; then
    echo "❌ GOOGLE_CLOUD_PROJECT not set"
    exit 1
fi

# List of secrets to create
SECRETS=(
    "gee-api-gemini-key"
    "gee-api-jwt-secret"
    "gee-api-redis-password"
)

# Create secrets if they don't exist
for secret in "${SECRETS[@]}"; do
    if ! gcloud secrets describe "$secret" --project="$PROJECT_ID" &gt;/dev/null; then
        echo "Creating secret: $secret"
        
        # Prompt for value if not set in environment
        var_name=$(echo "$secret" | tr '[:lower:]' '[:upper:]' | tr '-' '_')
        secret_value="${!var_name:-}"
        
        if [[ -z "$secret_value" ]]; then
            read -s -p "Enter value for $secret: " secret_value
            echo
        fi
        
        echo -n "$secret_value" | gcloud secrets create "$secret" \
            --project="$PROJECT_ID" \
            --replication-policy="automatic" \
            --data-file=- \
            --quiet
    else
        echo "Secret already exists: $secret"
    fi
done

# Grant access to Cloud Run service account
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-$PROJECT_ID-compute@developer.gserviceaccount.com}"

for secret in "${SECRETS[@]}"; do
    gcloud secrets add-iam-policy-binding "$secret" \
        --project="$PROJECT_ID" \
        --member="serviceAccount:$SERVICE_ACCOUNT" \
        --role="roles/secretmanager.secretAccessor" \
        --quiet 2&gt;/dev/null || true
done

echo "✅ Secrets configured successfully"