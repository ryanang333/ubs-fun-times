import base64
import binascii
import json
import math

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


def compute_slo(decoded_json):
    heartbeats = decoded_json.get("heartbeats") or []
    slo_query = decoded_json.get("sloQuery") or {}
    service = slo_query.get("service")
    since = slo_query.get("since")

    seen_keys = set()
    rows = []
    for heartbeat in heartbeats:
        if heartbeat.get("service") != service:
            continue
        timestamp = heartbeat.get("timestamp")
        if since is not None and timestamp < since:
            continue
        key = (heartbeat.get("service"), timestamp)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        rows.append(heartbeat)

    total = len(rows)
    if total == 0:
        return {"sloOutput": {"availability": 0.0, "p95LatencyMs": 0}}

    ok_count = sum(1 for row in rows if row.get("status") == "OK")
    availability = ok_count / total

    latencies = sorted(row.get("latencyMs", 0) for row in rows)
    rank = math.ceil(0.95 * total)
    p95_latency_ms = latencies[rank - 1]

    return {"sloOutput": {"availability": availability, "p95LatencyMs": p95_latency_ms}}


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

    response = {**adapt(decoded_json), **compute_slo(decoded_json)}
    return jsonify(response)
