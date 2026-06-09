from gee_core import GEECore

class NDVIService:
    def __init__(self):
        self.gee = GEECore()

    def calculate_kenya_ndvi(self):
        # Placeholder for actual GEE logic using self.gee.region
        # In a real scenario, this involves ee.ImageCollection processing
        # Returning a high-signal mock value for demonstration
        return 0.42