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

from .agent import investigate
from .routing import MAX_ADDRESS_CHARS, RoutingError
from .trip import plan_trip

STATIC_DIR = Path(__file__).parent / "static"
MAX_QUESTION_CHARS = 500
RUN_DIR = "runs"
NO_KEY_MESSAGE = (
    "Analyst needs an API key. Set ANTHROPIC_API_KEY and restart the server "
    "to ask follow-up questions. Trip planning still works without it."
)


def _analyst_client():
    """Model client for the analyst; None means 'let the SDK build one'.

    Exists so tests can monkeypatch in a fake client without an API key.
    """
    return None


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

    @app.get("/api/ask/stream")
    def ask_stream():
        """Run the 311 analyst agent on an open question, streamed as SSE.

        Mirrors /api/trip/stream: `step` events while the agent works, then one
        `result` event with the answer, or one `ask_error`. Every SQL query the
        model writes goes through validate_sql inside tools.run_sql.
        """
        question = request.args.get("q", "")
        if not isinstance(question, str) or not question.strip():
            return jsonify({"error": "Type a question first."}), 400
        if len(question) > MAX_QUESTION_CHARS:
            return jsonify({"error": "That question is too long."}), 400

        client = _analyst_client()
        events: queue.Queue = queue.Queue()

        if client is None and not os.environ.get("ANTHROPIC_API_KEY"):
            # The analyst has no fallback planner, so fail fast and friendly.
            events.put(("ask_error", {"error": NO_KEY_MESSAGE}))
            events.put(None)
        else:

            def worker() -> None:
                try:
                    answer = investigate(
                        question,
                        client=client,
                        on_event=lambda e: events.put(("step", e)),
                        run_dir=RUN_DIR,
                    )
                    events.put(("result", {"answer": answer}))
                except Exception:  # never leak internals to the browser
                    app.logger.exception("analyst question failed")
                    events.put(
                        ("ask_error", {"error": "The analyst hit an error. Try again."})
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
