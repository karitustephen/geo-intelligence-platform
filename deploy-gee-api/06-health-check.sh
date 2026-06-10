#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# HEALTH CHECK - Verify API is operational
# ============================================================

echo "❤️ Running health checks"

PORT="${PORT:-8080}"
HEALTH_URL="http://localhost:$PORT/health"
READY_URL="http://localhost:$PORT/ready"
MAX_RETRIES=30
RETRY_INTERVAL=2

# Function to check endpoint
check_endpoint() {
    local url=$1
    local expected_status=${2:-200}
    
    curl -s -o /dev/null -w "%{http_code}" "$url" 2&gt;/dev/null | grep -q "$expected_status"
}

# Wait for API to start
echo "Waiting for API to become healthy..."
for i in $(seq 1 $MAX_RETRIES); do
    if check_endpoint "$HEALTH_URL" 200; then
        echo "✅ API is healthy (attempt $i)"
        break
    fi
    
    if [[ $i -eq $MAX_RETRIES ]]; then
        echo "❌ API failed to become healthy after $MAX_RETRIES attempts"
        exit 1
    fi
    
    echo "⏳ Waiting for API... ($i/$MAX_RETRIES)"
    sleep $RETRY_INTERVAL
done

# Check readiness
echo "Checking readiness..."
if check_endpoint "$READY_URL" 200; then
    echo "✅ API is ready"
else
    echo "⚠️  API is healthy but not ready (dependencies may be initializing)"
fi

# Get detailed health info
echo "Fetching health details..."
curl -s "$HEALTH_URL" | jq '.' 2&gt;/dev/null || curl -s "$HEALTH_URL"

echo "✅ Health check passed"