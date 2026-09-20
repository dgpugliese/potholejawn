"""Tests for /api/ask/stream: the analyst question box. No API key or network."""

import json
from types import SimpleNamespace

from pothole_agent import webapp
from pothole_agent.webapp import create_app


def _block(**kwargs):
    return SimpleNamespace(**kwargs)


class FakeClient:
    """Replays scripted responses, like the fake client in test_agent.py."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.messages = self

    def create(self, **_):
        return self._responses.pop(0)


def _response(content, stop_reason):
    usage = SimpleNamespace(input_tokens=10, output_tokens=5)
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=usage)


def _sse_events(body):
    """Parse an SSE body into (event_name, payload) pairs."""
    events = []
    for chunk in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in chunk.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_ask_stream_validates_input():
    client = create_app().test_client()
    assert client.get("/api/ask/stream").status_code == 400
    assert client.get("/api/ask/stream?q=").status_code == 400
    assert client.get("/api/ask/stream?q=%20%20").status_code == 400
    assert client.get("/api/ask/stream?q=" + "a" * 501).status_code == 400


def test_ask_stream_without_key_is_a_friendly_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = create_app().test_client()
    response = client.get("/api/ask/stream?q=How+long+do+potholes+take%3F")
    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/event-stream")
    events = _sse_events(response.get_data(as_text=True))
    assert [name for name, _ in events] == ["ask_error"]
    assert "API key" in events[0][1]["error"]


def test_ask_stream_streams_steps_then_answer(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(webapp, "RUN_DIR", str(tmp_path))
    fake = FakeClient(
        [
            _response(
                [_block(type="tool_use", id="t1", name="get_schema", input={})],
                "tool_use",
            ),
            _response(
                [_block(type="text", text="Median close time is 12 days.")],
                "end_turn",
            ),
        ]
    )
    monkeypatch.setattr(webapp, "_analyst_client", lambda: fake)
    client = create_app().test_client()
    response = client.get("/api/ask/stream?q=How+long+to+fix+potholes+in+19143%3F")
    assert response.status_code == 200
    events = _sse_events(response.get_data(as_text=True))
    names = [name for name, _ in events]
    assert names[-1] == "result" and names.count("result") == 1
    assert names.count("step") >= 2, "agent steps should stream before the answer"
    tool_calls = [p for n, p in events if n == "step" and p.get("type") == "tool_call"]
    assert tool_calls and tool_calls[0]["tool"] == "get_schema"
    assert events[-1][1]["answer"] == "Median close time is 12 days."


def test_ask_stream_reports_agent_failure(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(webapp, "RUN_DIR", str(tmp_path))
    broken = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **_: (_ for _ in ()).throw(RuntimeError("boom")))
    )
    monkeypatch.setattr(webapp, "_analyst_client", lambda: broken)
    client = create_app().test_client()
    events = _sse_events(client.get("/api/ask/stream?q=hi").get_data(as_text=True))
    assert events[-1][0] == "ask_error"
    assert "boom" not in events[-1][1]["error"], "internals must not leak to the browser"
