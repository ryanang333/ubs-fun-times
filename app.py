import os

from flask import Flask

from routes.health import health
from routes.hello import hello
from routes.solve import solve
from routes.move import move


app = Flask(__name__)


def register_routes(flask_app: Flask) -> None:
    flask_app.add_url_rule("/", "hello", hello, methods=["GET"])
    flask_app.add_url_rule("/solve", "solve", solve, methods=["POST"])
    flask_app.add_url_rule("/health", "health", health, methods=["GET"])
    flask_app.add_url_rule("/move", "move", move, method=["POST"])


register_routes(app)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
