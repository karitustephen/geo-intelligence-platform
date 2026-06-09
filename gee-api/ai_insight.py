class AIInsightEngine:
    def analyze(self, value):
        if value is None:
            return "No Data"

        if value < 0.2:
            return "Low vegetation"
        elif value < 0.5:
            return "Moderate vegetation"
        return "High vegetation"
