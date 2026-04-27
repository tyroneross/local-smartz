"""Single-agent pattern — one turn, all tools. Baseline equivalent to the
existing DeepAgents single-agent flow, rewritten against ``AgentRunner``.

The legacy ``agent.run_research`` path is preserved for the production
research surface (which relies on DeepAgents internals — subagents,
SqliteSaver checkpointer, etc). This module exists so tests and the
pattern registry have a provider-agnostic entrypoint.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from localsmartz.patterns.base import PatternEvent, make_root_span
from localsmartz.runners import AgentRunner


async def run(
    prompt: str,
    *,
    agents: dict[str, dict[str, Any]],
    profile: dict[str, Any],
    stream: bool = True,
    runner: AgentRunner,
    ctx: dict[str, Any] | None = None,
) -> AsyncIterator[PatternEvent]:
    """Yield ``pattern_start``, one ``turn``, one ``done``."""
    thread_id = (ctx or {}).get("thread_id")
    span_cm, attrs = make_root_span("single", profile, thread_id)
    with span_cm as span:
        for k, v in attrs.items():
            span.set_attribute(k, v)

        yield {"type": "pattern_start", "pattern": "single"}

        primary = agents.get("primary") or next(iter(agents.values()), {})
        model_ref = primary.get("model_ref") or {"provider": "ollama", "name": profile.get("planning_model", "")}
        system = primary.get("system_focus", "")

        turn = await runner.run_turn(
            prompt,
            tools=primary.get("tools"),
            model_ref=model_ref,
            system=system,
            ctx=ctx,
        )

        yield {
            "type": "turn",
            "role": "primary",
            "content": turn.get("content", ""),
            "tool_calls": list(turn.get("tool_calls", []) or []),
        }
        yield {"type": "done", "thread_id": thread_id or ""}
