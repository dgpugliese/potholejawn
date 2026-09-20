"""Small Flask app: a map UI on top of the trip planner.

Run with:  python -m pothole_agent.webapp   then open http://127.0.0.1:5000
"""

from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from .routing import MAX_ADDRESS_CHARS, RoutingError
from .trip import plan_trip

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024  # requests are two short strings

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/healthz")
    def health():
        return jsonify({"ok": True, "agent_available": bool(os.environ.get("ANTHROPIC_API_KEY"))})

    @app.post("/api/trip")
    def trip():
        body = request.get_json(silent=True) or {}
        start, end = body.get("start"), body.get("end")
        for label, value in (("start", start), ("end", end)):
            if not isinstance(value, str) or not value.strip():
                return jsonify({"error": f"Enter a {label} address."}), 400
            if len(value) > MAX_ADDRESS_CHARS:
                return jsonify({"error": f"The {label} address is too long."}), 400
        use_agent = bool(body.get("use_agent", True)) and bool(os.environ.get("ANTHROPIC_API_KEY"))
        try:
            return jsonify(plan_trip(start, end, use_agent=use_agent))
        except RoutingError as error:
            return jsonify({"error": str(error)}), 400
        except Exception:  # never leak internals to the browser
            app.logger.exception("trip planning failed")
            return jsonify({"error": "A map or city data service is not responding. Try again."}), 502

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    return app


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)


if __name__ == "__main__":
    main()
