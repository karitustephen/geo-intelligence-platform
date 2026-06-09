from flask import jsonify

def success_response(data, message="Success", status_code=200):
    return jsonify({
        "status": "success",
        "message": message,
        "data": data
    }), status_code

def error_response(message, status_code=500, details=None):
    response = {
        "status": "error",
        "message": message
    }
    if details: response["details"] = details
    return jsonify(response), status_code