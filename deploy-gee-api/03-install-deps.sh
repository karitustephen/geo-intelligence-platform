#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# DEPENDENCY INSTALLATION - Python packages + system deps
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GEE_API_DIR="$SCRIPT_DIR/../gee-api"

echo "📦 Installing dependencies"

cd "$GEE_API_DIR"

# Activate virtual environment
source .venv/bin/activate

# Upgrade pip
pip install --upgrade pip setuptools wheel

# Install Python dependencies
if [[ -f "requirements.txt" ]]; then
    echo "Installing Python packages from requirements.txt..."
    pip install -r requirements.txt
else
    echo "❌ requirements.txt not found"
    exit 1
fi

# Install system dependencies (for OCR, PDF processing)
if command -v apt-get &gt;/dev/null 2&gt;&1; then
    echo "Installing system dependencies..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq \
        tesseract-ocr \
        tesseract-ocr-eng \
        poppler-utils \
        libgl1-mesa-glx \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev \
        libgomp1 \
        2&gt;/dev/null || true
fi

# Verify key packages
echo "Verifying critical packages..."
python3 -c "import fastapi; print(f'✓ FastAPI {fastapi.__version__}')"
python3 -c "import ee; print('✓ Earth Engine')"
python3 -c "import google; print('✓ Google AI')"

echo "✅ Dependencies installed successfully"