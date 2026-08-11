import json

from flask import jsonify, request

from kan_chiong_delivery_driver import solve


def kan_cheong_delivery_driver():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "request body must be a JSON object"}), 400

    try:
        return jsonify(json.loads(solve(json.dumps(payload))))
    except (KeyError, TypeError, ValueError, IndexError) as error:
        return jsonify({"error": f"invalid request: {error}"}), 400
