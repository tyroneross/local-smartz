"""Router pattern — classify the query, dispatch to a specialist.

The router turn returns a one-word label (or a tool call with a label arg);
the pattern maps the label to a specialist slot and runs one more turn.
Works well on small models because each stage sees a narrow context.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from localsmartz.patterns.base import PatternEvent, make_root_span
from localsmartz.runners import AgentRunner


DEFAULT_ROUTER_SYSTEM = (
    "You are a router. Read the user's question and respond with ONE word "
    "from the available specialist names. Do not add punctuation or "
    "explanation — just the word."
)


def _parse_label(content: str, allowed: list[str], default: str) -> str:
    """Return the first allowed label found in the router's output, else default."""
    if not isinstance(content, str):
        return default
    text = content.strip().lower().split()
    for word in text:
        clean = word.strip(".,;:!?\"'`")
        if clean in allowed:
            return clean
    # Also accept "specialist: <label>" shape.
    if ":" in content:
        _, _, tail = content.partition(":")
        word = tail.strip().split()[0].strip(".,;:!?\"'`").lower() if tail.strip() else ""
        if word in allowed:
            return word
    return default


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
    specialists = (ctx or {}).get("specialists") or []
    allowed = [s.get("label") for s in specialists if s.get("label")]
    default_label = allowed[0] if allowed else "generalist"

    span_cm, attrs = make_root_span("router", profile, thread_id)
    with span_cm as span:
        for k, v in attrs.items():
            span.set_attribute(k, v)

        yield {"type": "pattern_start", "pattern": "router"}

        router_slot = agents.get("router") or {}
        router_model = router_slot.get("model_ref") or {
            "provider": "ollama",
            "name": profile.get("planning_model", ""),
        }
        router_system = router_slot.get("system_focus") or (
            DEFAULT_ROUTER_SYSTEM
            + "\n\nAvailable specialists: " + ", ".join(allowed or [default_label])
        )

        router_turn = await runner.run_turn(
            prompt,
            model_ref=router_model,
            system=router_system,
            ctx=ctx,
        )
        label = _parse_label(router_turn.get("content", ""), allowed, default_label)
        span.set_attribute("ls.router.label", label)
        yield {
            "type": "turn",
            "role": "router",
            "content": router_turn.get("content", "") or "",
        }

        specialist_slot = next(
            (s for s in specialists if s.get("label") == label),
            None,
        ) or (specialists[0] if specialists else {})
        specialist_model = specialist_slot.get("model_ref") or agents.get(
            "specialist", {}
        ).get("model_ref") or router_model

        specialist_turn = await runner.run_turn(
            prompt,
            tools=specialist_slot.get("tools") or agents.get("specialist", {}).get("tools"),
            model_ref=specialist_model,
            system=specialist_slot.get("system_focus")
            or agents.get("specialist", {}).get("system_focus", ""),
            ctx=ctx,
        )
        yield {
            "type": "turn",
            "role": f"specialist:{label}",
            "content": specialist_turn.get("content", "") or "",
            "tool_calls": list(specialist_turn.get("tool_calls", []) or []),
        }
        yield {"type": "done", "thread_id": thread_id or ""}
