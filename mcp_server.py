"""FastMCP server exposing the tools required by mcp.md: name, sums, shapes."""

from __future__ import annotations

import ast
import base64
import operator
import os
import re

import cv2
import httpx
import numpy as np
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

mcp = FastMCP("ubs-fun-times")

_events: list[dict] = []


@mcp.custom_route("/event", methods=["POST"])
async def receive_event(request: Request) -> JSONResponse:
    """Telemetry sink: the evaluator POSTs one event per tool call attempt here."""
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    _events.append(payload)
    print(f"[event] problem={payload.get('problem')!r} attempt={payload.get('attempt')!r} {payload}")
    return JSONResponse({"received": True})


def _report(callback_url: str | None, result: dict) -> None:
    if not callback_url:
        return
    httpx.post(callback_url, json=result, timeout=5)


# ---------------------------------------------------------------------------
# name
# ---------------------------------------------------------------------------


@mcp.tool(name="name")
def name_tool(callback_url: str | None = None) -> str:
    """Return the agent's name."""
    result = "Bob"
    _report(callback_url, {"name": result})
    return result


# ---------------------------------------------------------------------------
# sums
# ---------------------------------------------------------------------------

_WORD_TO_SYMBOL = (
    (re.compile(r"\bmultiplied by\b"), "*"),
    (re.compile(r"\bdivided by\b"), "/"),
    (re.compile(r"\btimes\b"), "*"),
    (re.compile(r"\bplus\b"), "+"),
    (re.compile(r"\bminus\b"), "-"),
    (re.compile(r"\bover\b"), "/"),
)

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _normalize(text: str) -> str:
    normalized = text.lower().replace("×", "*").replace("÷", "/")
    for pattern, symbol in _WORD_TO_SYMBOL:
        normalized = pattern.sub(symbol, normalized)
    normalized = re.sub(r"(?<=[\d\s])x(?=\s*\d)", "*", normalized)
    return normalized


def _extract_expression(text: str) -> str:
    normalized = _normalize(text)
    candidates = re.findall(r"[0-9.+\-*/() ]+", normalized)
    expr = max((candidate.strip() for candidate in candidates), key=len, default="")
    if not expr:
        raise ValueError(f"no arithmetic expression found in: {text!r}")
    return expr


def _eval_node(node: ast.AST):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("unsupported expression")


def evaluate_arithmetic(question: str) -> int | float:
    expr = _extract_expression(question)
    result = _eval_node(ast.parse(expr, mode="eval"))
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return result


@mcp.tool(name="sums")
def sums_tool(question: str, callback_url: str | None = None) -> int | float:
    """Solve a simple arithmetic word problem, e.g. 'what is 2 + 2?'."""
    result = evaluate_arithmetic(question)
    _report(callback_url, {"result": result})
    return result


# ---------------------------------------------------------------------------
# shapes
# ---------------------------------------------------------------------------


def _decode_image(image_base64: str) -> np.ndarray:
    data = base64.b64decode(image_base64)
    array = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("could not decode base64 PNG")
    return image


def _foreground_mask(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    if cv2.countNonZero(mask) > mask.size / 2:
        mask = cv2.bitwise_not(mask)
    return mask


def _classify_contour(contour: np.ndarray) -> str:
    perimeter = cv2.arcLength(contour, True)
    if perimeter == 0:
        return "circle"

    approx = cv2.approxPolyDP(contour, 0.04 * perimeter, True)
    vertices = len(approx)
    if vertices == 3:
        return "triangle"
    if vertices == 4:
        return "rectangle"

    area = cv2.contourArea(contour)
    circularity = 4 * np.pi * area / (perimeter * perimeter)
    return "circle" if circularity > 0.7 else "rectangle"


def classify_shapes(image_base64: str) -> list[str]:
    image = _decode_image(image_base64)
    mask = _foreground_mask(image)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contours = [c for c in contours if cv2.contourArea(c) > 20]
    return [_classify_contour(c) for c in contours]


@mcp.tool(name="shapes")
def shapes_tool(image_base64: str, callback_url: str | None = None) -> dict:
    """Decode a base64 PNG and classify each shape as rectangle, triangle or circle."""
    shapes = classify_shapes(image_base64)
    result = {
        "shapes": shapes,
        "count": len(shapes),
        "shape": shapes[0] if len(shapes) == 1 else None,
    }
    _report(callback_url, result)
    return result


if __name__ == "__main__":
    mcp.run(
        transport="http",
        path="/mcp",
        host="0.0.0.0",
        port=int(os.environ.get("MCP_PORT", 8001)),
        stateless_http=True,
        json_response=True,
    )
