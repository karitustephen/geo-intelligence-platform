import ee

try:
    ee.Initialize()
except Exception:
    ee.Authenticate()
    ee.Initialize()

class GEECore:
    def __init__(self):
        self.region = ee.Geometry.Rectangle([33.5, -4.8, 41.9, 5.2])
