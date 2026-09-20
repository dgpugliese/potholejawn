"""Agent loop tests using a fake model client: no API key or network needed."""

from types import SimpleNamespace

from pothole_agent import agent


def _block(**kwargs):
    return SimpleNamespace(**kwargs)


class FakeClient:
    """Replays scripted responses and records what the agent sent back."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


def _response(content, stop_reason):
    usage = SimpleNamespace(input_tokens=10, output_tokens=5)
    return SimpleNamespace(content=content, stop_reason=stop_reason, usage=usage)


def test_guardrail_violation_is_returned_to_model_as_error(tmp_path):
    client = FakeClient(
        [
            _response(
                [
                    _block(
                        type="tool_use",
                        id="t1",
                        name="run_sql",
                        input={"query": "DROP TABLE public_cases_fc", "purpose": "evil"},
                    )
                ],
                "tool_use",
            ),
            _response([_block(type="text", text="I cannot do that. Final report.")], "end_turn"),
        ]
    )
    events = []
    report = agent.investigate("q", client=client, on_event=events.append, run_dir=tmp_path)

    assert "Final report" in report
    # The message list is shared and keeps growing, so find the tool result by role.
    user_turns = [m for m in client.calls[1]["messages"] if m["role"] == "user"]
    tool_result = user_turns[-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "GuardrailError" in tool_result["content"]
    assert any(e["type"] == "tool_error" for e in events)
    assert list(tmp_path.glob("run-*.jsonl")), "run log should be written"


def test_step_limit_stops_runaway_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "MAX_STEPS", 3)
    looping = [
        _response([_block(type="tool_use", id=f"t{i}", name="get_schema", input={})], "tool_use")
        for i in range(3)
    ]
    report = agent.investigate("q", client=FakeClient(looping), run_dir=tmp_path)
    assert "step limit" in report


def test_unknown_tool_does_not_crash():
    text, is_error = agent.execute_tool("launch_rockets", {})
    assert is_error and "Unknown tool" in text
