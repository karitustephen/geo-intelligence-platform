#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# ENVIRONMENT VALIDATION - Check all prerequisites
# ============================================================

echo "🔍 Validating environment"

# Check required commands
REQUIRED_CMDS=("python3" "pip3" "curl" "jq")
if [[ -z "${K_SERVICE:-}" ]]; then
    REQUIRED_CMDS+=("gcloud" "docker")
fi

MISSING_CMDS=()
for cmd in "${REQUIRED_CMDS[@]}"; do
    if ! command -v "$cmd" &gt;/dev/null 2&gt;&1; then
        MISSING_CMDS+=("$cmd")
    fi
done

if [[ ${#MISSING_CMDS[@]} -gt 0 ]]; then
    echo "❌ Missing required commands: ${MISSING_CMDS[*]}"
    exit 1
fi

# Check Python version
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo "❌ Python 3.9+ required (found $PYTHON_VERSION)"
    exit 1
fi

# Check critical environment variables
REQUIRED_VARS=()
OPTIONAL_VARS=("GEMINI_API_KEY" "JWT_SECRET" "REDIS_HOST")

if [[ "${ENVIRONMENT:-development}" == "production" ]]; then
    REQUIRED_VARS+=("GEMINI_API_KEY" "JWT_SECRET")
fi

MISSING_VARS=()
for var in "${REQUIRED_VARS[@]}"; do
    if [[ -z "${!var:-}" ]]; then
        MISSING_VARS+=("$var")
    fi
done

if [[ ${#MISSING_VARS[@]} -gt 0 ]]; then
    echo "❌ Missing required environment variables: ${MISSING_VARS[*]}"
    echo "   Please set them in .env file or environment"
    exit 1
fi

# Optional vars warning
for var in "${OPTIONAL_VARS[@]}"; do
    if [[ -z "${!var:-}" ]]; then
        echo "⚠️  Optional variable not set: $var"
    fi
done

# Check disk space
AVAILABLE_GB=$(df / | awk 'NR==2 {print $4/1024/1024}')
if (( $(echo "$AVAILABLE_GB &lt; 2" | bc -l) )); then
    echo "⚠️  Low disk space: ${AVAILABLE_GB}GB available"
fi

echo "✅ Environment validation passed"
echo "   Python: $PYTHON_VERSION"
echo "   Environment: ${ENVIRONMENT:-development}"