import os

from flask import Flask

from routes.hello import hello


app = Flask(__name__)


def register_routes(flask_app: Flask) -> None:
    flask_app.add_url_rule("/", "hello", hello, methods=["GET"])


register_routes(app)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
