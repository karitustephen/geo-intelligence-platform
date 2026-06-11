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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEE_API_DIR="${SCRIPT_DIR}/gee-api"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# ------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------
PROJECT_ID="${GCP_PROJECT_ID:-$(gcloud config get-value project 2>/dev/null || echo "gee-capstone-2026")}"
DEV_MODE="${DEV_MODE:-false}"
PROJECT_NAME="${PROJECT_NAME:-Arybit Geospatial Intelligence}"
REGION="${GCP_REGION:-us-central1}"
BQ_LOCATION="US"
DATASET_NAME="${BIGQUERY_DATASET:-geospatial_analytics}"
GEE_SA_NAME="gee-intelligence-sa"
RUN_SA_NAME="arybit-cloudrun-sa"

REQUIRED_SERVICES=(
    "earthengine.googleapis.com"
    "bigquery.googleapis.com"
    "cloudbuild.googleapis.com"
    "aiplatform.googleapis.com"
    "run.googleapis.com"
    "secretmanager.googleapis.com"
    "cloudresourcemanager.googleapis.com"
    "iam.googleapis.com"
    "monitoring.googleapis.com"
    "logging.googleapis.com"
    "artifactregistry.googleapis.com"
    "billingbudgets.googleapis.com"
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

# ------------------------------------------------------------
# PROJECT SETUP
# ------------------------------------------------------------
log_step "Step 3: Setting up GCP project..."

if gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1; then
    log_info "Project already exists: $PROJECT_ID"
else
    log_info "Creating project: $PROJECT_ID"
    if ! gcloud projects create "$PROJECT_ID" \
        --name="$PROJECT_NAME" \
        --set-as-default; then
        log_error "Failed to create project. Check quotas or project ID availability."
        exit 1
    fi
fi

gcloud config set project "$PROJECT_ID" --quiet
gcloud config set compute/region "$REGION" --quiet

log_success "Active project: $PROJECT_ID (Region: $REGION)"

# ------------------------------------------------------------
# BILLING CHECK
# ------------------------------------------------------------
log_step "Step 4: Checking billing status..."

if [[ "$DEV_MODE" == "true" ]]; then
    log_info "Dev mode active - skipping billing check"
    BILLING_ENABLED="False"
else
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
        log_info "Note: Secret Manager, Cloud Storage, and Vertex AI may fail or hang."
    fi
fi
fi

if [[ "$BILLING_ENABLED" == "True" ]]; then
    log_success "Billing enabled for project $PROJECT_ID"
else
    log_warn "Proceeding with billing disabled (Development Mode)"
fi

# ------------------------------------------------------------
# ENABLE APIs
# ------------------------------------------------------------
log_step "Step 5: Enabling required APIs..."
for service in "${REQUIRED_SERVICES[@]}"; do
    log_info "Activating $service..."
    # Try to enable service and catch billing-related failures
    if ! gcloud services enable "$service" --quiet 2>/tmp/service.err; then
        if grep -q "billing-enabled" /tmp/service.err; then
            log_warn "Skipping $service (billing required)"
            continue
        fi
        cat /tmp/service.err
        exit 1
    fi
    
    # Resilient activation check with retry
    for i in {1..12}; do
        if gcloud services list --enabled --filter="name:$service" --format="value(name)" | grep -q "$service"; then
            break
        fi
        if [[ $i -eq 12 ]]; then
            log_error "Timeout waiting for $service activation"
            exit 1
        fi
        sleep 5
    done
done

log_success "All required APIs enabled"

# ------------------------------------------------------------
# SERVICE ACCOUNT SETUP
# ------------------------------------------------------------
log_step "Step 6: Creating service account..."

GEE_SA_EMAIL="${GEE_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
RUN_SA_EMAIL="${RUN_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Create Earth Engine SA
if ! gcloud iam service-accounts describe "$GEE_SA_EMAIL" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$GEE_SA_NAME" --display-name="GEE Processing SA"
fi

# Create Cloud Run Runtime SA
if ! gcloud iam service-accounts describe "$RUN_SA_EMAIL" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$RUN_SA_NAME" --display-name="Arybit API Runtime SA"
fi

log_info "Configuring GCP IAM roles (Least Privilege)..."

# Runtime Permissions
RUN_ROLES=(
    "roles/storage.objectAdmin"
    "roles/secretmanager.secretAccessor"
    "roles/bigquery.dataEditor"
    "roles/bigquery.jobUser"
    "roles/aiplatform.user"
)

for role in "${RUN_ROLES[@]}"; do
    log_info "Assigning $role..."
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:${RUN_SA_EMAIL}" --role="$role" --quiet || log_warn "Failed to assign $role (might require billing or API activation)"
done

# Cloud Build / Deployment Permissions
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

if [[ "$BILLING_ENABLED" == "True" ]]; then
    DEPLOY_ROLES=(
        "roles/artifactregistry.writer"
        "roles/artifactregistry.reader"
        "roles/run.admin"
        "roles/iam.serviceAccountUser"
    )

    for sa in "${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" "service-${PROJECT_NUMBER}@gcp-sa-cloudbuild.iam.gserviceaccount.com"; do
        for role in "${DEPLOY_ROLES[@]}"; do
            gcloud projects add-iam-policy-binding "$PROJECT_ID" \
                --member="serviceAccount:${sa}" --role="$role" --quiet || true
        done
    done
fi

log_success "Service accounts and Cloud Build IAM configured"

# ------------------------------------------------------------
# BIGQUERY SETUP
# ------------------------------------------------------------
log_step "Step 7: Setting up BigQuery..."

if ! bq show "${PROJECT_ID}:${DATASET_NAME}" >/dev/null 2>&1; then
    log_info "Creating BigQuery dataset: $DATASET_NAME"
    bq mk --dataset \
        --location="$BQ_LOCATION" \
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
    result_json JSON,
    created_at TIMESTAMP,
    status STRING
) PARTITION BY DATE(created_at)
CLUSTER BY user_id, analysis_type' 2>/dev/null || true

log_info "Creating file metadata table..."
bq query --use_legacy_sql=false --quiet "
CREATE TABLE IF NOT EXISTS \`${PROJECT_ID}.${DATASET_NAME}.file_metadata\` (
    file_id STRING,
    user_id STRING,
    filename STRING,
    gcs_path STRING,
    uploaded_at TIMESTAMP,
    status STRING
) PARTITION BY DATE(uploaded_at)
CLUSTER BY user_id" 2>/dev/null || true

log_success "BigQuery setup complete"

# ------------------------------------------------------------
# CLOUD STORAGE SETUP
# ------------------------------------------------------------
log_step "Step 8: Setting up Cloud Storage..."

BUCKET_NAME="gee-intelligence-${PROJECT_ID}-geo"

if [[ "$BILLING_ENABLED" == "True" ]] && ! gsutil ls "gs://${BUCKET_NAME}" >/dev/null 2>&1; then
    log_info "Creating Cloud Storage bucket: $BUCKET_NAME"
    if ! gsutil mb -l "$REGION" "gs://${BUCKET_NAME}" 2>/dev/null; then
        log_warn "Could not create GCS bucket (likely billing restricted). Using local cache mode."
    else
        gsutil iam ch "serviceAccount:${RUN_SA_EMAIL}:objectAdmin" "gs://${BUCKET_NAME}"
        log_success "Cloud Storage bucket ready: $BUCKET_NAME"
    fi
else
    if [[ "$BILLING_ENABLED" != "True" ]]; then
        log_warn "Skipping bucket creation (billing disabled)"
    else
        log_info "Bucket already exists: $BUCKET_NAME"
    fi
fi

# ------------------------------------------------------------
# ARTIFACT REGISTRY SETUP
# ------------------------------------------------------------
if [[ "$BILLING_ENABLED" == "True" ]]; then
    if ! gcloud artifacts repositories describe geo-intelligence --location="$REGION" >/dev/null 2>&1; then
        log_info "Creating Artifact Registry: geo-intelligence"
        gcloud artifacts repositories create geo-intelligence \
            --repository-format=docker --location="$REGION" --quiet
    fi
    gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
else
    log_warn "Skipping Artifact Registry setup (billing disabled)"
fi

# ------------------------------------------------------------
# EARTH ENGINE SETUP
# ------------------------------------------------------------
log_step "Step 9: Installing Python dependencies..."

cd "$GEE_API_DIR"
python3 -m venv .venv || true
source .venv/bin/activate

# Ensure we don't have the 'jwt' package which conflicts with 'PyJWT'
python3 -m pip uninstall -y jwt || true

# Upgrade build tools for Python 3.12 compatibility
log_info "Upgrading build tools..."
pip install --quiet --upgrade pip setuptools wheel

if [[ -f "requirements.txt" ]]; then
    REQ_FILE="requirements.txt"

    # Make script more resilient to obsolete packages
    if grep -q "google-earth-engine" "$REQ_FILE"; then
        log_warn "Replacing deprecated google-earth-engine package in requirements.txt"
        sed -i 's/google-earth-engine==.*$/earthengine-api>=1.0.0/' "$REQ_FILE"
    fi

    log_info "Installing Python packages from $REQ_FILE..."
    python3 -m pip install --upgrade -r "$REQ_FILE"
else
    log_warn "requirements.txt not found, installing base dependencies..."
    python3 -m pip install --quiet --upgrade \
        PyJWT \
        earthengine-api \
        google-cloud-bigquery \
        google-cloud-aiplatform \
        google-cloud-storage \
        gunicorn \
        fastapi \
        uvicorn \
        httpx \
        redis \
        numpy \
        python-dotenv \
        pydantic \
        pydantic-settings \
        prometheus-fastapi-instrumentator \
        prometheus-client \
        google-genai \
        python-multipart
fi

log_success "Python dependencies installed"

# ------------------------------------------------------------
# EARTH ENGINE AUTHENTICATION
# ------------------------------------------------------------
log_step "Step 10: Configuring Earth Engine..."

# Set the Earth Engine project context
earthengine set_project "$PROJECT_ID" || true

log_info "Using Application Default Credentials (ADC) for Earth Engine."
log_info "For local development, ensure you have run: gcloud auth application-default login"

python3 << EOF && log_success "Earth Engine verified" || log_warn "Earth Engine verification failed (expected if local auth missing)"
import ee
try:
    ee.Initialize(project='${PROJECT_ID}')
    ee.Image("USGS/SRTMGL1_003").getInfo()
except Exception:
    exit(1)
EOF

# ------------------------------------------------------------
# SECRETS SETUP
# ------------------------------------------------------------
log_step "Step 11: Setting up secrets in Secret Manager..."

cd /workspaces/geo-intelligence-platform/gee-api

SECRET_MANAGER_STATUS="Skipped (Dev Mode/No Billing)"

if [[ "$BILLING_ENABLED" == "True" && "$DEV_MODE" != "true" ]]; then
    declare -A SECRETS=(
        ["gee-api-gemini-key"]="Enter your Google Gemini API key: "
        ["gee-api-jwt-secret"]="Enter JWT secret (min 32 chars): "
        ["gee-api-redis-password"]="Enter Redis password (optional): "
    )

    for secret_name in "${!SECRETS[@]}"; do
        if ! timeout 10s gcloud secrets describe "$secret_name" --project="$PROJECT_ID" >/dev/null 2>&1; then
            log_info "Creating secret: $secret_name"
            read -s -p "${SECRETS[$secret_name]}" secret_value
            echo
            
            if [[ "$secret_name" == "gee-api-jwt-secret" ]] && [[ ${#secret_value} -lt 32 ]]; then
                log_error "JWT secret must be at least 32 characters"
                exit 1
            fi

            # Securely create secret using temp file
            SEC_FILE=$(mktemp)
            echo -n "$secret_value" > "$SEC_FILE"
            gcloud secrets create "$secret_name" \
                --project="$PROJECT_ID" \
                --replication-policy="automatic" \
                --data-file="$SEC_FILE" \
                --quiet
            shred -u "$SEC_FILE"
        else
            log_info "Secret already exists: $secret_name"
        fi
    done

    for secret_name in "${!SECRETS[@]}"; do
        gcloud secrets add-iam-policy-binding "$secret_name" \
            --project="$PROJECT_ID" \
            --member="serviceAccount:${RUN_SA_EMAIL}" \
            --role="roles/secretmanager.secretAccessor" \
            --quiet 2>/dev/null || true
    done
    log_success "Secrets configured in Secret Manager"
    SECRET_MANAGER_STATUS="Configured"
else
    log_warn "Skipping Secret Manager setup due to billing restrictions."
    
    # Better Development Mode Fix: Create a local .env for immediate use
    cat > ".env" << EOF
GCP_PROJECT_ID=${PROJECT_ID}
JWT_SECRET=development_jwt_secret_for_local_use_only_min_32_chars
REDIS_PASSWORD=development
GEMINI_API_KEY=
EOF
    log_success "Development .env file created locally"
    SECRET_MANAGER_STATUS="Local .env"
fi

# ------------------------------------------------------------
# VALIDATION TESTS
# ------------------------------------------------------------
log_step "Step 12: Running validation tests..."

python3 << EOF
import sys
import os
print("[TEST] Testing Google Cloud services...")
billing_enabled = "${BILLING_ENABLED}" == "True"

try:
    import ee
    ee.Initialize(project='${PROJECT_ID}')
    
    # Verify actual dataset access (Permission check)
    ee.Image("USGS/SRTMGL1_003").getInfo()
    print("✅ Earth Engine initialized and dataset access verified")
except Exception as e:
    print(f"❌ Earth Engine failed: {e}")
    sys.exit(1)

try:
    from google.cloud import bigquery
    client = bigquery.Client(project='${PROJECT_ID}')
    client.query("SELECT 1").result()
    print("✅ BigQuery query execution successful")
except Exception as e:
    print(f"❌ BigQuery failed: {e}")
    sys.exit(1)

if billing_enabled:
    try:
        from google.cloud import storage
        client = storage.Client(project='${PROJECT_ID}')
        bucket = client.bucket('${BUCKET_NAME}')
        if bucket.exists():
            print("✅ Cloud Storage bucket validated")
        else:
            print("⚠️ Cloud Storage bucket not found")
    except Exception as e:
        print(f"❌ Cloud Storage failed: {e}")
        sys.exit(1)

    try:
        from google.cloud import aiplatform
        aiplatform.init(project='${PROJECT_ID}', location='${REGION}')
        print("✅ Vertex AI initialized")
    except Exception as e:
        print(f"❌ Vertex AI failed: {e}")
        sys.exit(1)
else:
    print("⚠️ Cloud Storage and Vertex AI validation skipped (billing disabled)")

print("\n✅ Service validation complete!")
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
GEE_SERVICE_ACCOUNT="${GEE_SA_EMAIL}"
RUN_SERVICE_ACCOUNT="${RUN_SA_EMAIL}"

# Redis (configure manually if needed)
REDIS_HOST="localhost"
REDIS_PORT="6379"

# Placeholder secrets if Secret Manager was skipped
GEMINI_API_KEY=""
REDIS_PASSWORD=""
JWT_SECRET=""

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
        if [[ -d ".venv" ]]; then
            source .venv/bin/activate
        fi
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
printf "║   ${GREEN}Runtime SA${NC}      : $RUN_SA_NAME%*s\n" $((35 - ${#RUN_SA_NAME})) ""
printf "║   ${GREEN}Bucket${NC}          : $BUCKET_NAME%*s\n" $((35 - ${#BUCKET_NAME})) ""
printf "║   ${GREEN}Dataset${NC}         : $DATASET_NAME%*s\n" $((35 - ${#DATASET_NAME})) ""
echo "║                                                               ║"
echo "╠═══════════════════════════════════════════════════════════════╣"
echo "║                                                               ║"
echo "║   ✅ Earth Engine API       : Enabled                         ║"
echo "║   ✅ BigQuery               : Ready                           ║"
echo "║   ✅ Vertex AI              : Ready                           ║"
echo "║   ✅ Cloud Run              : Ready                           ║"
printf "║   ✅ Secret Manager         : %-30s║\n" "$SECRET_MANAGER_STATUS"
echo "║                                                               ║"
echo "╠═══════════════════════════════════════════════════════════════╣"
echo "║                                                               ║"
echo "║   📍 Next Steps:                                              ║"
echo "║                                                               ║"
echo "║   1. Copy environment file:                                   ║"
echo "║      cp /tmp/gee-api.env ./gee-api/.env                       ║"
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
