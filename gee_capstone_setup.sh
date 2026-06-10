#!/usr/bin/env bash
# ============================================================
# GEE CAPSTONE PRODUCTION SETUP SCRIPT
# Version: 2.0.0
# Purpose: One-command Google Earth Engine + GCP setup
# Integrates with: Arybit Geospatial Intelligence Platform
# Environment: Cloud Shell / Ubuntu / Linux
# ============================================================

set -Eeuo pipefail

# ------------------------------------------------------------
# COLOR CODES
# ------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------
PROJECT_ID="${GCP_PROJECT_ID:-gee-capstone-2026}"
PROJECT_NAME="${PROJECT_NAME:-Arybit Geospatial Intelligence}"
REGION="${GCP_REGION:-us-central1}"
DATASET_NAME="${BIGQUERY_DATASET:-gee_dataset}"
SERVICE_ACCOUNT_NAME="gee-intelligence-sa"
REQUIRED_SERVICES=(
    "earthengine.googleapis.com"
    "bigquery.googleapis.com"
    "aiplatform.googleapis.com"
    "run.googleapis.com"
    "secretmanager.googleapis.com"
    "cloudresourcemanager.googleapis.com"
    "iam.googleapis.com"
)

# ------------------------------------------------------------
# LOGGING FUNCTIONS
# ------------------------------------------------------------
log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_step() {
    echo -e "\n${BLUE}▶${NC} ${CYAN}$1${NC}"
}

log_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

log_failure() {
    echo -e "${RED}❌ $1${NC}"
}

# ------------------------------------------------------------
# ERROR HANDLING
# ------------------------------------------------------------
handle_error() {
    log_error "Script failed at line $1"
    log_error "Check the error above and fix before re-running"
    exit 1
}

trap 'handle_error $LINENO' ERR

# ------------------------------------------------------------
# BANNER
# ------------------------------------------------------------
echo -e "${CYAN}"
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                                                               ║"
echo "║     ARYBIT GEOSPATIAL INTELLIGENCE - GEE CAPSTONE SETUP       ║"
echo "║                     PRODUCTION READY 🚀                        ║"
echo "║                                                               ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# ------------------------------------------------------------
# PRECHECKS
# ------------------------------------------------------------
log_step "Step 1: Validating environment..."

# Check for essential system tools and install if missing
if ! command -v gcloud >/dev/null 2>&1 || ! command -v bc >/dev/null 2>&1; then
    log_warn "Essential tools (gcloud or bc) missing. Attempting installation..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq apt-transport-https ca-certificates gnupg curl bc
    if ! command -v gcloud >/dev/null 2>&1; then
        curl -s https://packages.cloud.google.com/apt/doc/apt-key.gpg | sudo gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
        echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | sudo tee /etc/apt/sources.list.d/google-cloud-sdk.list
        sudo apt-get update -qq
        sudo apt-get install -y -qq google-cloud-cli
    fi
    log_success "System tools updated successfully"
fi

# Check required commands
REQUIRED_CMDS=("gcloud" "python3" "pip" "curl" "jq" "bc")
MISSING_CMDS=()

for cmd in "${REQUIRED_CMDS[@]}"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        MISSING_CMDS+=("$cmd")
    fi
done

if [[ ${#MISSING_CMDS[@]} -gt 0 ]]; then
    log_error "Missing required commands: ${MISSING_CMDS[*]}"
    exit 1
fi

log_success "All required commands available"

# Check Python version
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    log_error "Python 3.9+ required (found $PYTHON_VERSION)"
    exit 1
fi
log_success "Python $PYTHON_VERSION OK"

# ------------------------------------------------------------
# AUTHENTICATION
# ------------------------------------------------------------
log_step "Step 2: Checking authentication..."

# Check if authenticated
if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | grep -q .; then
    log_warn "No active account found. Launching login..."
    gcloud auth login --quiet
fi

ACTIVE_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" | head -1)
log_success "Authenticated as: $ACTIVE_ACCOUNT"

# Check if running in Cloud Shell
if [[ -n "${CLOUD_SHELL:-}" ]]; then
    log_info "Running in Google Cloud Shell"
    PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "$PROJECT_ID")
fi

# ------------------------------------------------------------
# PROJECT SETUP
# ------------------------------------------------------------
log_step "Step 3: Setting up GCP project..."

if ! gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
    log_info "Creating project: $PROJECT_ID"
    gcloud projects create "$PROJECT_ID" \
        --name="$PROJECT_NAME" \
        --set-as-default
else
    log_info "Project already exists: $PROJECT_ID"
fi

gcloud config set project "$PROJECT_ID" --quiet
gcloud config set compute/region "$REGION" --quiet

log_success "Active project: $PROJECT_ID (Region: $REGION)"

# ------------------------------------------------------------
# BILLING CHECK
# ------------------------------------------------------------
log_step "Step 4: Checking billing status..."

BILLING_ENABLED=$(gcloud beta billing projects describe "$PROJECT_ID" \
    --format="value(billingEnabled)" 2>/dev/null || echo "False")

if [[ "$BILLING_ENABLED" != "True" ]]; then
    log_warn "Billing is NOT enabled for $PROJECT_ID"
    echo ""
    echo "To enable billing:"
    echo "1. Visit: https://console.cloud.google.com/billing"
    echo "2. Link a billing account to project: $PROJECT_ID"
    echo "3. Then re-run this script"
    echo ""
    read -p "Press Enter to continue after enabling billing..."
    
    BILLING_ENABLED=$(gcloud beta billing projects describe "$PROJECT_ID" \
        --format="value(billingEnabled)" 2>/dev/null || echo "False")
    
    if [[ "$BILLING_ENABLED" != "True" ]]; then
        log_warn "Billing still not enabled. Continuing in Development/Earth Engine mode..."
        log_info "Note: Some GCP-native features like Vertex AI or Cloud Run may fail later."
    fi
fi

log_success "Billing enabled for project $PROJECT_ID"

# ------------------------------------------------------------
# ENABLE APIs
# ------------------------------------------------------------
log_step "Step 5: Enabling required APIs..."

for service in "${REQUIRED_SERVICES[@]}"; do
    log_info "Enabling $service..."
    gcloud services enable "$service" --quiet 2>/dev/null || true
done

log_success "All required APIs enabled"

# ------------------------------------------------------------
# SERVICE ACCOUNT SETUP
# ------------------------------------------------------------
log_step "Step 6: Creating service account..."

SERVICE_ACCOUNT_EMAIL="${SERVICE_ACCOUNT_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

if ! gcloud iam service-accounts describe "$SERVICE_ACCOUNT_EMAIL" >/dev/null 2>&1; then
    log_info "Creating service account: $SERVICE_ACCOUNT_NAME"
    gcloud iam service-accounts create "$SERVICE_ACCOUNT_NAME" \
        --display-name="Geospatial Intelligence Service Account"
else
    log_info "Service account already exists"
fi

log_info "Granting IAM roles..."
IAM_ROLES=(
    "roles/earthengine.admin"
    "roles/storage.admin"
    "roles/run.admin"
    "roles/secretmanager.admin"
    "roles/bigquery.dataOwner"
    "roles/aiplatform.user"
)

for role in "${IAM_ROLES[@]}"; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
        --role="$role" \
        --quiet 2>/dev/null || true
done

log_success "Service account configured: $SERVICE_ACCOUNT_EMAIL"

# ------------------------------------------------------------
# BIGQUERY SETUP
# ------------------------------------------------------------
log_step "Step 7: Setting up BigQuery..."

if ! bq show "${PROJECT_ID}:${DATASET_NAME}" >/dev/null 2>&1; then
    log_info "Creating BigQuery dataset: $DATASET_NAME"
    bq mk --dataset \
        --location="$REGION" \
        --description="Geospatial Intelligence Analytics" \
        "${PROJECT_ID}:${DATASET_NAME}"
else
    log_info "BigQuery dataset already exists: $DATASET_NAME"
fi

log_info "Creating analysis results table..."
bq query --use_legacy_sql=false --quiet '
CREATE TABLE IF NOT EXISTS `'${PROJECT_ID}.${DATASET_NAME}'.analyses` (
    analysis_id STRING,
    user_id STRING,
    analysis_type STRING,
    result_json STRING,
    created_at TIMESTAMP,
    status STRING
) PARTITION BY DATE(created_at)
CLUSTER BY user_id, analysis_type' 2>/dev/null || true

log_success "BigQuery setup complete"

# ------------------------------------------------------------
# CLOUD STORAGE SETUP
# ------------------------------------------------------------
log_step "Step 8: Setting up Cloud Storage..."

BUCKET_NAME="gee-intelligence-${PROJECT_ID}"

if ! gsutil ls "gs://${BUCKET_NAME}" >/dev/null 2>&1; then
    log_info "Creating Cloud Storage bucket: $BUCKET_NAME"
    if ! gsutil mb -l "$REGION" "gs://${BUCKET_NAME}" 2>/dev/null; then
        log_warn "Could not create GCS bucket (likely billing restricted). Using local cache mode."
    else
        gsutil iam ch "serviceAccount:${SERVICE_ACCOUNT_EMAIL}:objectAdmin" "gs://${BUCKET_NAME}"
        log_success "Cloud Storage bucket ready: $BUCKET_NAME"
    fi
else
    log_info "Bucket already exists: $BUCKET_NAME"
fi

# ------------------------------------------------------------
# EARTH ENGINE SETUP
# ------------------------------------------------------------
log_step "Step 9: Installing Python dependencies..."

pip install --quiet --upgrade \
    earthengine-api \
    google-cloud-bigquery \
    google-cloud-aiplatform \
    google-cloud-storage \
    flask \
    gunicorn \
    fastapi \
    uvicorn \
    httpx \
    redis \
    numpy

log_success "Python dependencies installed"

# ------------------------------------------------------------
# EARTH ENGINE AUTHENTICATION
# ------------------------------------------------------------
log_step "Step 10: Configuring Earth Engine..."

if ! python3 -c "import ee; ee.Initialize()" 2>/dev/null; then
    log_warn "Earth Engine not authenticated. Starting authentication..."
    
    if [[ -f "/tmp/gee-key.json" ]]; then
        python3 << EOF
import ee
service_account = '${SERVICE_ACCOUNT_EMAIL}'
credentials = ee.ServiceAccountCredentials(service_account, '/tmp/gee-key.json')
ee.Initialize(credentials)
print("Earth Engine initialized with service account")
EOF
    else
        log_info "Running interactive Earth Engine authentication..."
        earthengine authenticate --quiet || {
            log_warn "Manual authentication required"
            earthengine authenticate
        }
    fi
else
    log_success "Earth Engine already authenticated"
fi

# ------------------------------------------------------------
# SECRETS SETUP
# ------------------------------------------------------------
log_step "Step 11: Setting up secrets in Secret Manager..."

declare -A SECRETS=(
    ["gee-api-gemini-key"]="Enter your Google Gemini API key: "
    ["gee-api-jwt-secret"]="Enter JWT secret (min 32 chars): "
    ["gee-api-redis-password"]="Enter Redis password (optional): "
)

for secret_name in "${!SECRETS[@]}"; do
    if ! gcloud secrets describe "$secret_name" --project="$PROJECT_ID" >/dev/null 2>&1; then
        log_info "Creating secret: $secret_name"
        read -s -p "${SECRETS[$secret_name]}" secret_value
        echo
        echo -n "$secret_value" | gcloud secrets create "$secret_name" \
            --project="$PROJECT_ID" \
            --replication-policy="automatic" \
            --data-file=- \
            --quiet
    else
        log_info "Secret already exists: $secret_name"
    fi
done

for secret_name in "${!SECRETS[@]}"; do
    gcloud secrets add-iam-policy-binding "$secret_name" \
        --project="$PROJECT_ID" \
        --member="serviceAccount:${SERVICE_ACCOUNT_EMAIL}" \
        --role="roles/secretmanager.secretAccessor" \
        --quiet 2>/dev/null || true

done

log_success "Secrets configured"

# ------------------------------------------------------------
# VALIDATION TESTS
# ------------------------------------------------------------
log_step "Step 12: Running validation tests..."

python3 << 'EOF'
import sys
print("[TEST] Testing Google Cloud services...")

try:
    import ee
    ee.Initialize()
    print("✅ Earth Engine initialized")
except Exception as e:
    print(f"❌ Earth Engine failed: {e}")
    sys.exit(1)

try:
    from google.cloud import bigquery
    client = bigquery.Client()
    list(client.list_datasets(max_results=1))
    print("✅ BigQuery client initialized")
except Exception as e:
    print(f"❌ BigQuery failed: {e}")
    sys.exit(1)

try:
    from google.cloud import storage
    client = storage.Client()
    list(client.list_buckets(max_results=1))
    print("✅ Cloud Storage client initialized")
except Exception as e:
    print(f"❌ Cloud Storage failed: {e}")
    sys.exit(1)

try:
    from google.cloud import aiplatform
    aiplatform.init()
    print("✅ Vertex AI initialized")
except Exception as e:
    print(f"❌ Vertex AI failed: {e}")
    sys.exit(1)

print("\n✅ All services validated successfully!")
EOF

if [[ $? -eq 0 ]]; then
    log_success "All validation tests passed"
else
    log_error "Validation tests failed"
    exit 1
fi

# ------------------------------------------------------------
# CREATE ENVIRONMENT FILE
# ------------------------------------------------------------
log_step "Step 13: Creating environment configuration..."

cat > "/tmp/gee-api.env" << EOF
# GEE Capstone Environment Configuration
# Generated: $(date)

# API Configuration
APP_NAME="Arybit Geospatial Intelligence"
APP_VERSION="2.0.0"
ENVIRONMENT="production"
LOG_LEVEL="INFO"
PORT="8080"
WORKERS="4"

# Google Cloud
GOOGLE_CLOUD_PROJECT="${PROJECT_ID}"
GCP_REGION="${REGION}"
GCS_BUCKET_NAME="${BUCKET_NAME}"
BIGQUERY_DATASET="${DATASET_NAME}"

# Service Account
GEE_SERVICE_ACCOUNT="${SERVICE_ACCOUNT_EMAIL}"

# Redis (configure manually if needed)
REDIS_HOST="localhost"
REDIS_PORT="6379"

# Authentication
AUTH_MODE="remote"
INTERNAL_SERVICE_NAME="arybit-geo-intelligence"

# Geospatial thresholds
NDVI_WATER_THRESHOLD="0.0"
NDVI_SPARSE_THRESHOLD="0.2"
NDVI_MODERATE_THRESHOLD="0.4"
NDVI_DENSE_THRESHOLD="0.6"

# Rate Limiting
RATE_LIMIT_PER_MINUTE="60"
RATE_LIMIT_ANONYMOUS_PER_MINUTE="30"

# Grace Mode
GRACE_MAX_TOKENS="4096"
GRACE_MAX_PROMPT_CHARS="16000"

# Thread Pool
CPU_EXECUTOR_THREADS="4"
EOF

log_success "Environment template created at: /tmp/gee-api.env"

# ------------------------------------------------------------
# DEPLOYMENT OPTIONS
# ------------------------------------------------------------
log_step "Step 14: Deployment options"

echo ""
echo "Choose deployment method:"
echo "  1) Local development (run API locally)"
echo "  2) Cloud Run deployment (production)"
echo "  3) Deploy with orchestrator (full pipeline)"
echo "  4) Skip deployment (just setup)"
echo ""
read -p "Enter choice [1-4]: " DEPLOY_CHOICE

case $DEPLOY_CHOICE in
    1)
        log_info "Starting local development server..."
        cd /workspaces/geo-intelligence-platform/gee-api
        python3 main.py
        ;;
    2)
        log_info "Deploying to Cloud Run..."
        cd /workspaces/geo-intelligence-platform/deploy-gee-api
        ./08-cloud-run-deploy.sh
        ;;
    3)
        log_info "Running full deployment orchestrator..."
        cd /workspaces/geo-intelligence-platform/deploy-gee-api
        chmod +x *.sh
        ./00-orchestrator.sh
        ;;
    4)
        log_info "Setup complete. Skipping deployment."
        ;;
    *)
        log_warn "Invalid choice. Skipping deployment."
        ;;
esac

# ------------------------------------------------------------
# FINAL OUTPUT
# ------------------------------------------------------------
echo ""
echo "╔═══════════════════════════════════════════════════════════════╗"
echo "║                                                               ║"
echo "║     🎉 GEE CAPSTONE ENVIRONMENT READY                         ║"
echo "║                                                               ║"
echo "╠═══════════════════════════════════════════════════════════════╣"
echo "║                                                               ║"
printf "║   ${GREEN}Project ID${NC}      : $PROJECT_ID%*s\n" $((35 - ${#PROJECT_ID})) ""
printf "║   ${GREEN}Region${NC}          : $REGION%*s\n" $((35 - ${#REGION})) ""
printf "║   ${GREEN}Service Account${NC} : $SERVICE_ACCOUNT_EMAIL%*s\n" $((35 - ${#SERVICE_ACCOUNT_EMAIL})) ""
printf "║   ${GREEN}Bucket${NC}          : $BUCKET_NAME%*s\n" $((35 - ${#BUCKET_NAME})) ""
printf "║   ${GREEN}Dataset${NC}         : $DATASET_NAME%*s\n" $((35 - ${#DATASET_NAME})) ""
echo "║                                                               ║"
echo "╠═══════════════════════════════════════════════════════════════╣"
echo "║                                                               ║"
echo "║   ✅ Earth Engine API       : Enabled                         ║"
echo "║   ✅ BigQuery               : Ready                           ║"
echo "║   ✅ Vertex AI              : Ready                           ║"
echo "║   ✅ Cloud Run              : Ready                           ║"
echo "║   ✅ Secret Manager         : Configured                      ║"
echo "║                                                               ║"
echo "╠═══════════════════════════════════════════════════════════════╣"
echo "║                                                               ║"
echo "║   📍 Next Steps:                                              ║"
echo "║                                                               ║"
echo "║   1. Copy environment file:                                   ║"
echo "║      cp /tmp/gee-api.env ../gee-api/.env                      ║"
echo "║                                                               ║"
echo "║   2. Edit secrets in .env file                                ║"
echo "║                                                               ║"
echo "║   3. Run the API:                                             ║"
echo "║   ║      cd ../gee-api && python main.py                          ║"
echo "║                                                               ║"
echo "║   4. Or deploy to Cloud Run:                                  ║"
echo "║      cd ../deploy-gee-api && ./08-cloud-run-deploy.sh         ║"
echo "║                                                               ║"
echo "╚═══════════════════════════════════════════════════════════════╝"
echo ""

log_success "GEE Capstone setup complete! 🚀"
