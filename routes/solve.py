import base64
import binascii
import json

from flask import jsonify, request

PRIORITY_MAP = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
DEFAULT_PRIORITY = 2


def adapt(decoded_json):
    adapt_input = decoded_json.get("adaptInput", {})
    user = adapt_input.get("user", {})
    metadata = adapt_input.get("metadata", {})

    priority = PRIORITY_MAP.get(metadata.get("priority"), DEFAULT_PRIORITY)

    return {
        "adaptOutput": {
            "id": user.get("id"),
            "name": user.get("fullName"),
            "action": str(adapt_input.get("action", "")).lower(),
            "priority": priority,
        }
    }


def solve():
    body = request.get_json(silent=True) or {}
    payload = body.get("payload")

    if not isinstance(payload, str):
        return jsonify({"error": "'payload' must be a base64-encoded string"}), 400

    try:
        decoded_bytes = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return jsonify({"error": "'payload' is not valid base64"}), 400

    try:
        decoded_json = json.loads(decoded_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return jsonify({"error": "decoded payload is not valid JSON"}), 400

    if not isinstance(decoded_json, dict):
        return jsonify({"error": "decoded payload must be a JSON object"}), 400

    return jsonify(adapt(decoded_json))
