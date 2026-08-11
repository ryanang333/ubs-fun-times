from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from ghost_chains import parse_timestamp, processor


ghost_chains = Blueprint("ghost_chains", __name__, url_prefix="/ghost-chains")


@ghost_chains.get("/health")
def health():
    return jsonify({"status": "ok"})


@ghost_chains.post("/reset")
def reset():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body.get("clearTransactions") is not True:
        return jsonify({"error": "clearTransactions must be true"}), 400

    processor.reset()
    return jsonify({"clearTransactions": True})


@ghost_chains.post("/transactions")
def transactions():
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("transactions"), list):
        return jsonify({"error": "transactions must be an array"}), 400

    for transaction in body["transactions"]:
        error = _validate_transaction(transaction)
        if error is not None:
            return jsonify({"error": error}), 400

    results = processor.process_batch(body["transactions"])
    return jsonify({"transactions": results})


def _validate_transaction(transaction: Any) -> str | None:
    if not isinstance(transaction, dict):
        return "each transaction must be an object"

    for field in ("txId", "fromUserId", "toUserId", "createdAt"):
        if not isinstance(transaction.get(field), str) or not transaction[field]:
            return f"{field} must be a non-empty string"

    amount = transaction.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return "amount must be a number"

    try:
        parse_timestamp(transaction["createdAt"])
    except ValueError:
        return "createdAt must be a valid ISO 8601 timestamp with a timezone"

    for field in ("ipAddress", "deviceId"):
        if field in transaction and not isinstance(transaction[field], str):
            return f"{field} must be a string when provided"

    return None
