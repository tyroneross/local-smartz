"""Single-agent pattern — one turn, all tools. Baseline equivalent to the
existing DeepAgents single-agent flow, rewritten against ``AgentRunner``.

The legacy ``agent.run_research`` path is preserved for the production
research surface (which relies on DeepAgents internals — subagents,
SqliteSaver checkpointer, etc). This module exists so tests and the
pattern registry have a provider-agnostic entrypoint.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from localsmartz.patterns.base import BudgetTracker, PatternEvent, make_root_span
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
    """Yield ``pattern_start``, one ``turn``, one ``done``.

    When ``runner`` exposes ``stream_turn`` AND ``stream=True``, yields
    one ``text_delta`` PatternEvent per token chunk before the final
    ``turn`` event. This bridges the c6 cloud-runner streaming primitive
    (cloud_anthropic.stream_turn / cloud_openai_compat.stream_turn) into
    the pattern event stream, matching the local-Ollama UX.

    Falls back to ``run_turn`` (whole-turn flush) when:
    - ``stream`` is False, OR
    - the runner doesn't expose ``stream_turn`` (e.g. local_ollama,
      harmony — these don't ship a streaming surface yet).
    """
    thread_id = (ctx or {}).get("thread_id")
    budget = BudgetTracker()
    span_cm, attrs = make_root_span("single", profile, thread_id)
    with span_cm as span:
        for k, v in attrs.items():
            span.set_attribute(k, v)

        yield {"type": "pattern_start", "pattern": "single"}

        primary = agents.get("primary") or next(iter(agents.values()), {})
        model_ref = primary.get("model_ref") or {"provider": "ollama", "name": profile.get("planning_model", "")}
        system = primary.get("system_focus", "")

        # Prefer streaming when available + requested. Falls back to run_turn
        # for runners that don't ship stream_turn (local_ollama, harmony) or
        # when the caller has disabled streaming explicitly.
        use_streaming = stream and hasattr(runner, "stream_turn")

        if use_streaming:
            assembled_content = ""
            final_turn: dict = {}
            async for chunk in runner.stream_turn(  # type: ignore[attr-defined]
                prompt,
                tools=primary.get("tools"),
                model_ref=model_ref,
                system=system,
                ctx=ctx,
            ):
                if chunk.get("done"):
                    final_turn = chunk.get("final") or {}
                    if chunk.get("error"):
                        # Surface the error as a turn payload so the caller
                        # gets a deterministic terminal event.
                        final_turn = {
                            "content": f"[error] {chunk.get('error')}",
                            "tool_calls": [],
                            "usage": {},
                        }
                    break
                delta = chunk.get("delta", "")
                if delta:
                    assembled_content += delta
                    yield {
                        "type": "text_delta",
                        "role": "primary",
                        "delta": delta,
                    }
            turn = final_turn or {
                "content": assembled_content,
                "tool_calls": [],
                "usage": {},
            }
        else:
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
        warn = budget.tick(turn.get("usage"), model_ref.get("provider", "ollama"))
        if warn is not None:
            yield warn
        yield {"type": "done", "thread_id": thread_id or ""}
