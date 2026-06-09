#!/bin/bash

# Docker run script for local development

# Load environment variables
source ../.env

# Build the Docker image
docker build -t geo-intelligence-api:latest ..

# Run the container
docker run -d \
  --name geo-intelligence-api \
  -p 8000:8000 \
  -e ENVIRONMENT=production \
  -e LOG_LEVEL=INFO \
  -e GEMINI_API_KEY=$GEMINI_API_KEY \
  -e GEE_SERVICE_ACCOUNT=$GEE_SERVICE_ACCOUNT \
  -e GEE_PRIVATE_KEY_PATH=/app/gee-key.json \
  -e JWT_SECRET=$JWT_SECRET \
  -e REDIS_HOST=$REDIS_HOST \
  -e REDIS_PORT=$REDIS_PORT \
  -v $(pwd)/gee-key.json:/app/gee-key.json:ro \
  geo-intelligence-api:latest

echo "Container started on port 8000"