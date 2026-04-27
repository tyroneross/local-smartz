"""Prompt-chaining pattern — linear stages.

Default stages: ``gather`` → ``analyze`` → ``write``. Each stage takes the
previous stage's output as its user prompt. Each stage can be pinned to a
different model via ``agents[stage].model_ref``; by default all stages
share the ``primary`` slot (so mini tier keeps one model resident).
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from localsmartz.patterns.base import PatternEvent, make_root_span
from localsmartz.runners import AgentRunner


DEFAULT_STAGES: list[dict[str, str]] = [
    {
        "name": "gather",
        "system": (
            "You are the GATHER stage. Read the user's question and write a "
            "concise, factual notes document covering the raw inputs needed. "
            "Do not analyze yet."
        ),
    },
    {
        "name": "analyze",
        "system": (
            "You are the ANALYZE stage. Read the prior notes and produce a "
            "structured set of findings — numbers, comparisons, trade-offs. "
            "Do not write the final answer yet."
        ),
    },
    {
        "name": "write",
        "system": (
            "You are the WRITE stage. Compose the final user-facing answer "
            "using pyramid-principle short-form: governing thought first, "
            "then 2-4 MECE key lines with support."
        ),
    },
]


async def run(
    prompt: str,
    *,
    agents: dict[str, dict[str, Any]],
    profile: dict[str, Any],
    stream: bool = True,
    runner: AgentRunner,
    ctx: dict[str, Any] | None = None,
) -> AsyncIterator[PatternEvent]:
    thread_id = (ctx or {}).get("thread_id")
    stages = (ctx or {}).get("stages") or DEFAULT_STAGES

    span_cm, attrs = make_root_span("chain", profile, thread_id)
    with span_cm as span:
        for k, v in attrs.items():
            span.set_attribute(k, v)

        yield {"type": "pattern_start", "pattern": "chain"}

        primary = agents.get("primary") or next(iter(agents.values()), {})
        current_input = prompt

        for stage in stages:
            slot = agents.get(stage["name"], primary)
            model_ref = slot.get("model_ref") or {
                "provider": "ollama",
                "name": profile.get("planning_model", ""),
            }
            turn = await runner.run_turn(
                current_input,
                tools=slot.get("tools"),
                model_ref=model_ref,
                system=slot.get("system_focus") or stage.get("system", ""),
                ctx=ctx,
            )
            content = turn.get("content", "") or ""
            yield {
                "type": "turn",
                "role": stage["name"],
                "content": content,
                "tool_calls": list(turn.get("tool_calls", []) or []),
            }
            current_input = content  # Feed into next stage.

        yield {"type": "done", "thread_id": thread_id or ""}
