from flask import Blueprint
from utils.response import success_response

health_bp = Blueprint('health', __name__)

@health_bp.route("/health")
def health():
    return success_response({"service": "gee-api"}, message="Service is healthy")