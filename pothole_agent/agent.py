"""The agent loop: plan, query, self-correct, and report."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .tools import TOOL_FUNCTIONS, TOOL_SPECS

DEFAULT_MODEL = "claude-sonnet-5"
MAX_STEPS = 12
MAX_TOOL_RESULT_CHARS = 12000

SYSTEM_PROMPT = """\
You are an accountability analyst for Philadelphia's 311 system. A resident,
journalist, or council staffer asks a question; you investigate the city's
public data and report what you find. Today is {today}.

How you work:
1. Call get_schema first. Briefly state your plan (2 to 4 steps) before querying.
2. Investigate with several focused queries rather than one giant one. Compare
   across zip codes and across time when it helps answer the question.
3. If a query errors, read the error, fix the SQL, and retry.
4. Finish with a short report: the headline finding, the supporting numbers,
   and a "Caveats" line. Never state a number you did not get from a query.

Be honest about uncertainty. Open cases have no close date, so closed-case
averages understate real waits; say so when relevant. 311 volume reflects who
reports, not only where problems are; do not claim a neighborhood "has more
potholes" when the data only shows more reports.
"""

Logger = Callable[[dict[str, Any]], None]


def _make_run_logger(run_dir: Path) -> tuple[Logger, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / f"run-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"

    def log(event: dict[str, Any]) -> None:
        event = {"t": round(time.time(), 2), **event}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, default=str) + "\n")

    return log, path


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    functions: dict[str, Callable[..., Any]] | None = None,
) -> tuple[str, bool]:
    """Run one tool call. Returns (result_text, is_error). Never raises."""
    function = (TOOL_FUNCTIONS if functions is None else functions).get(name)
    if function is None:
        return f"Unknown tool: {name}", True
    try:
        result = function(**arguments)
    except Exception as error:  # surfaced to the model so it can self-correct
        return f"{type(error).__name__}: {error}", True
    text = json.dumps(result, default=str)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + "... [truncated: aggregate more in SQL]"
    return text, False


PLAIN_TEXT_SUFFIX = (
    "\n\nYour final report is shown on a plain-text web page: do not use any "
    "markdown syntax (no #, **, backticks, or tables). Use short paragraphs "
    "and simple lines starting with '-' for lists."
)


def investigate(
    question: str,
    client: Any = None,
    on_event: Logger | None = None,
    run_dir: Path | str = "runs",
    plain_text: bool = False,
) -> str:
    """Run a full 311 data investigation and return the final report text."""
    today = datetime.now(tz=UTC).date().isoformat()
    system_prompt = SYSTEM_PROMPT.format(today=today)
    if plain_text:
        system_prompt += PLAIN_TEXT_SUFFIX
    return run_agent(
        question,
        system_prompt=system_prompt,
        tool_specs=TOOL_SPECS,
        tool_functions=TOOL_FUNCTIONS,
        client=client,
        on_event=on_event,
        run_dir=run_dir,
    )


def _move_cache_breakpoint(messages: list[dict[str, Any]]) -> None:
    """Keep exactly one ephemeral cache breakpoint: on the newest user message.

    The API allows at most 4 breakpoints per request, and the loop grows the
    message list every step, so the breakpoint has to move rather than pile up.
    """
    for message in messages:
        if message["role"] == "user" and isinstance(message["content"], list):
            for block in message["content"]:
                if isinstance(block, dict):
                    block.pop("cache_control", None)
    last = messages[-1]
    if last["role"] == "user" and isinstance(last["content"], list) and last["content"]:
        block = last["content"][-1]
        if isinstance(block, dict):
            block["cache_control"] = {"type": "ephemeral"}


def run_agent(
    question: str,
    system_prompt: str,
    tool_specs: list[dict[str, Any]],
    tool_functions: dict[str, Callable[..., Any]],
    client: Any = None,
    on_event: Logger | None = None,
    run_dir: Path | str = "runs",
    model: str | None = None,
) -> str:
    """Generic tool-calling loop shared by every agent in this project."""
    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    log, log_path = _make_run_logger(Path(run_dir))

    def emit(event: dict[str, Any]) -> None:
        log(event)
        if on_event:
            on_event(event)

    model = model or os.environ.get("POTHOLE_MODEL", DEFAULT_MODEL)
    # One breakpoint on the system prompt caches the whole stable prefix
    # (tools render before system), so repeat requests read it at ~10% price.
    system_blocks = [
        {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}
    ]
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": [{"type": "text", "text": question}]}
    ]
    emit({"type": "question", "text": question, "model": model, "log": str(log_path)})

    for step in range(1, MAX_STEPS + 1):
        _move_cache_breakpoint(messages)
        response = client.messages.create(
            model=model,
            max_tokens=2000,
            system=system_blocks,
            tools=tool_specs,
            messages=messages,
        )
        emit(
            {
                "type": "usage",
                "step": step,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0),
                "cache_creation_input_tokens": getattr(
                    response.usage, "cache_creation_input_tokens", 0
                ),
            }
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type == "text" and block.text.strip():
                emit({"type": "thought", "step": step, "text": block.text})
            elif block.type == "tool_use":
                emit({"type": "tool_call", "step": step, "tool": block.name, "input": block.input})
                text, is_error = execute_tool(block.name, dict(block.input), tool_functions)
                emit(
                    {
                        "type": "tool_error" if is_error else "tool_result",
                        "step": step,
                        "tool": block.name,
                        "preview": text[:400],
                    }
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": text,
                        "is_error": is_error,
                    }
                )

        if response.stop_reason != "tool_use":
            final = "\n".join(b.text for b in response.content if b.type == "text")
            emit({"type": "final", "steps": step})
            return final
        messages.append({"role": "user", "content": tool_results})

    emit({"type": "stopped", "reason": f"hit MAX_STEPS={MAX_STEPS}"})
    return "Stopped: the agent hit its step limit before finishing."
