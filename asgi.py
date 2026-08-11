"""Single ASGI entrypoint serving the Flask REST API and the FastMCP server
on one port: Flask keeps its existing routes, MCP is mounted at /mcp and
/event (see mcp_server.py). Run with `python asgi.py` or
`uvicorn asgi:app`.
"""

import os

from asgiref.wsgi import WsgiToAsgi
from starlette.applications import Starlette
from starlette.routing import Mount

from app import app as flask_app
from mcp_server import mcp

mcp_app = mcp.http_app(path="/mcp", stateless_http=True, json_response=True)

app = Starlette(
    routes=[*mcp_app.routes, Mount("/", app=WsgiToAsgi(flask_app))],
    lifespan=mcp_app.lifespan,
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
