#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# API STARTUP - Gunicorn with Uvicorn workers
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEE_API_DIR="$SCRIPT_DIR/../gee-api"

echo "🚀 Starting GEE API from main.py"

cd "$GEE_API_DIR"

# Activate virtual environment
source .venv/bin/activate

# Set production environment variables
export ENVIRONMENT="${ENVIRONMENT:-production}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export PORT="${PORT:-8080}"
export WORKERS="${WORKERS:-4}"
export TIMEOUT="${TIMEOUT:-120}"

# Create logs directory
mkdir -p /var/log/gee-api

# Determine run mode
if [[ -n "${K_SERVICE:-}" ]]; then
    # Cloud Run mode
    echo "Running in Cloud Run mode"
    exec gunicorn \
        --workers "$WORKERS" \
        --worker-class uvicorn.workers.UvicornWorker \
        --bind "0.0.0.0:$PORT" \
        --timeout "$TIMEOUT" \
        --access-logfile - \
        --error-logfile - \
        --log-level "$LOG_LEVEL" \
        main:app
else
    # Local development mode
    echo "Running in local development mode"
    
    # Check if using uvicorn directly (for dev)
    if [[ "${DEV_MODE:-false}" == "true" ]]; then
        exec uvicorn \
            main:app \
            --host 0.0.0.0 \
            --port "$PORT" \
            --reload \
            --log-level "$LOG_LEVEL"
    else
        exec gunicorn \
            --workers "$WORKERS" \
            --worker-class uvicorn.workers.UvicornWorker \
            --bind "0.0.0.0:$PORT" \
            --timeout "$TIMEOUT" \
            --access-logfile /var/log/gee-api/access.log \
            --error-logfile /var/log/gee-api/error.log \
            --log-level "$LOG_LEVEL" \
            main:app
    fi
fi