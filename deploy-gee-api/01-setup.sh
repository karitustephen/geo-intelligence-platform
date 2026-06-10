#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# ENVIRONMENT SETUP - Create directories, set permissions
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEE_API_DIR="$SCRIPT_DIR/../gee-api"

echo "🔧 Setting up environment"

# Create necessary directories
mkdir -p /tmp/gee-uploads /tmp/gee-cache /var/log/gee-api

# Set proper permissions
chmod 755 /tmp/gee-uploads /tmp/gee-cache

# Create Python virtual environment if not exists
if [[ ! -d "$GEE_API_DIR/.venv" ]]; then
    echo "Creating Python virtual environment..."
    cd "$GEE_API_DIR"
    python3 -m venv .venv
fi

# Load environment variables from .env if exists
if [[ -f "$GEE_API_DIR/.env" ]]; then
    echo "Loading environment variables from .env"
    set -a
    source "$GEE_API_DIR/.env"
    set +a
fi

# Set default environment
export ENVIRONMENT="${ENVIRONMENT:-development}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

echo "✅ Environment setup complete"
echo "   Environment: $ENVIRONMENT"
echo "   Log Level: $LOG_LEVEL"