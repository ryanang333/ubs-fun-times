from flask import jsonify, request


def square():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400

    number = payload.get("number")
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        return jsonify({"error": "number must be numeric"}), 400

    return jsonify({"answer": number * number})
