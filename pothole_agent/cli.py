"""Command-line entry point: python -m pothole_agent.cli "your question"."""

from __future__ import annotations

import sys
from typing import Any

DEFAULT_QUESTION = (
    "Where in Philadelphia is the city slowest at fixing potholes this year, "
    "and is it getting better or worse compared to last year?"
)


def print_event(event: dict[str, Any]) -> None:
    kind = event["type"]
    if kind == "question":
        print(f"\nQUESTION: {event['text']}\n(model: {event['model']}, log: {event['log']})\n")
    elif kind == "thought":
        print(f"[step {event['step']}] {event['text']}\n")
    elif kind == "tool_call":
        detail = event["input"].get("purpose") or event["input"]
        print(f"[step {event['step']}] -> {event['tool']}: {detail}")
        if "query" in event["input"]:
            print(f"    SQL: {event['input']['query']}")
    elif kind == "tool_error":
        print(f"    !! {event['preview']}  (agent will retry)\n")
    elif kind == "tool_result":
        print(f"    ok: {event['preview'][:160]}...\n")
    elif kind == "stopped":
        print(f"STOPPED: {event['reason']}")


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    from .agent import investigate

    question = " ".join(sys.argv[1:]).strip() or DEFAULT_QUESTION
    report = investigate(question, on_event=print_event)
    # Thoughts stream as they happen, so the final report is already on screen.
    if not report:
        print("(no report produced)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
