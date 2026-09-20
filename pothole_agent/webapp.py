"""Small Flask app: a map UI on top of the trip planner.

Run with:  python -m pothole_agent.webapp   then open http://127.0.0.1:5000
"""

from __future__ import annotations

import json
import os
import queue
import threading
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

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

    def validate_addresses(start, end) -> str | None:
        """Return an error message for the browser, or None if the input is fine."""
        for label, value in (("start", start), ("end", end)):
            if not isinstance(value, str) or not value.strip():
                return f"Enter a {label} address."
            if len(value) > MAX_ADDRESS_CHARS:
                return f"The {label} address is too long."
        return None

    @app.post("/api/trip")
    def trip():
        body = request.get_json(silent=True) or {}
        start, end = body.get("start"), body.get("end")
        problem = validate_addresses(start, end)
        if problem:
            return jsonify({"error": problem}), 400
        use_agent = bool(body.get("use_agent", True)) and bool(os.environ.get("ANTHROPIC_API_KEY"))
        try:
            return jsonify(plan_trip(start, end, use_agent=use_agent))
        except RoutingError as error:
            return jsonify({"error": str(error)}), 400
        except Exception:  # never leak internals to the browser
            app.logger.exception("trip planning failed")
            return jsonify({"error": "A map or city data service is not responding. Try again."}), 502

    @app.get("/api/trip/stream")
    def trip_stream():
        """Same planner as /api/trip, but streamed as Server-Sent Events.

        Emits `step` events as the agent (or the fallback planner) works, then
        one `result` event with the full trip payload, or one `trip_error`.
        """
        start, end = request.args.get("start"), request.args.get("end")
        problem = validate_addresses(start, end)
        if problem:
            return jsonify({"error": problem}), 400
        use_agent = bool(request.args.get("use_agent", "1") != "0") and bool(
            os.environ.get("ANTHROPIC_API_KEY")
        )
        events: queue.Queue = queue.Queue()

        def worker() -> None:
            try:
                result = plan_trip(
                    start, end, use_agent=use_agent, on_step=lambda e: events.put(("step", e))
                )
                events.put(("result", result))
            except RoutingError as error:
                events.put(("trip_error", {"error": str(error)}))
            except Exception:  # never leak internals to the browser
                app.logger.exception("trip planning failed")
                events.put(
                    ("trip_error", {"error": "A map or city data service is not responding. Try again."})
                )
            events.put(None)  # sentinel: stream is done

        threading.Thread(target=worker, daemon=True).start()

        def generate():
            while True:
                item = events.get()
                if item is None:
                    return
                kind, payload = item
                yield f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"

        return Response(
            generate(),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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
