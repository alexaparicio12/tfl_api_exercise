from flask import Flask, jsonify, request
import requests
from urllib.parse import quote


def create_app() -> Flask:
    app = Flask(__name__)

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify(status="ok")

    @app.route("/hello", methods=["GET"])
    def hello():
        return jsonify(message="hello world")

    @app.route("/status", methods=["GET"])
    def status():
        """
        Proxy to the TFL Line Status API.
        Call with:
          - ?lines=victoria
          - ?lines=victoria,central
        """
        raw_lines = request.args.get("lines", "")
        line_args = [part.strip() for part in raw_lines.split(",") if part.strip()]

        if not line_args:
            return jsonify(error="Provide lines via ?lines=a or ?lines=a,b"), 400

        encoded_lines = quote(",".join(line_args), safe="")

        url = f"https://api.tfl.gov.uk/Line/{encoded_lines}/Status"
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
        except requests.RequestException as exc:
            return jsonify(error="failed to reach TFL API", detail=str(exc)), 502

        return jsonify(response.json())

    return app


if __name__ == "__main__":
    application = create_app()
    application.run(host="0.0.0.0", port=5555)
