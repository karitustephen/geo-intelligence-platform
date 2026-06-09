from flask import Blueprint
from services.ndvi_service import NDVIService
from utils.response import success_response

ndvi_bp = Blueprint('ndvi', __name__)
ndvi_service = NDVIService()

@ndvi_bp.route("/ndvi")
def get_ndvi():
    ndvi_value = ndvi_service.calculate_kenya_ndvi()
    return success_response({"region": "Kenya", "ndvi": ndvi_value})