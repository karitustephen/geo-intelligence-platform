#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# ARYBIT GEOSPATIAL INTELLIGENCE - MASTER DEPLOYMENT ORCHESTRATOR
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_FILE="/tmp/gee-deploy-$(date +%Y%m%d-%H%M%S).log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1" | tee -a "$LOG_FILE"
}

log_success() {
    echo -e "${GREEN}[✓] $1${NC}" | tee -a "$LOG_FILE"
}

log_error() {
    echo -e "${RED}[✗] $1${NC}" | tee -a "$LOG_FILE"
}

log_warning() {
    echo -e "${YELLOW}[!] $1${NC}" | tee -a "$LOG_FILE"
}

log_section() {
    echo -e "\n${BLUE}═══════════════════════════════════════════════════════════${NC}" | tee -a "$LOG_FILE"
    echo -e "${BLUE}  $1${NC}" | tee -a "$LOG_FILE"
    echo -e "${BLUE}═══════════════════════════════════════════════════════════${NC}\n" | tee -a "$LOG_FILE"
}

# Error handling
handle_error() {
    log_error "Deployment failed at line $1"
    log_error "Check log file: $LOG_FILE"
    exit 1
}

trap 'handle_error $LINENO' ERR

# Main execution
main() {
    log_section "ARYBIT GEOSPATIAL INTELLIGENCE DEPLOYMENT"
    log "Starting deployment at $(date)"
    log "Log file: $LOG_FILE"
    
    # Check if running in Cloud Run
    if [[ -n "${K_SERVICE:-}" ]]; then
        log_warning "Detected Cloud Run environment - running in production mode"
        export ENVIRONMENT="production"
    fi
    
    # Execute deployment steps
    log_section "STEP 1: Environment Setup"
    bash "$SCRIPT_DIR/01-setup.sh" || exit 1
    
    log_section "STEP 2: Environment Validation"
    bash "$SCRIPT_DIR/02-env-check.sh" || exit 1
    
    log_section "STEP 3: Dependency Installation"
    bash "$SCRIPT_DIR/03-install-deps.sh" || exit 1
    
    log_section "STEP 4: GCP Authentication"
    bash "$SCRIPT_DIR/04-auth-gcp.sh" || exit 1
    
    log_section "STEP 5: API Startup"
    bash "$SCRIPT_DIR/05-run-api.sh" &
    API_PID=$!
    
    log_section "STEP 6: Health Check"
    sleep 5
    bash "$SCRIPT_DIR/06-health-check.sh" || exit 1
    
    log_section "DEPLOYMENT COMPLETE"
    log_success "GEE API is running (PID: $API_PID)"
    log_success "Log file: $LOG_FILE"
    
    # Wait for API process if not in background
    if [[ -z "${K_SERVICE:-}" ]]; then
        wait $API_PID
    fi
}

# Run main function
main "$@"