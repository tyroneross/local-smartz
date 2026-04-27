"""Pattern protocol + shared event shapes.

Every pattern is an ``async def run(...)`` that yields ``PatternEvent`` dicts.
Callers (HTTP SSE, CLI) stream the events; the final event is always
``{"type": "done", ...}``.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable, Protocol, TypedDict

from localsmartz.runners import AgentRunner


class PatternEvent(TypedDict, total=False):
    type: str          # "pattern_start" | "turn" | "tool" | "iteration" | "score" | "done"
    pattern: str
    role: str
    content: str
    tool_calls: list[dict[str, Any]]
    iteration: int
    score: float
    verdict: str
    thread_id: str
    duration_ms: int


class Pattern(Protocol):
    async def run(
        self,
        prompt: str,
        *,
        agents: dict[str, dict[str, Any]],
        profile: dict[str, Any],
        stream: bool,
        runner: AgentRunner,
        ctx: dict[str, Any] | None = None,
    ) -> AsyncIterator[PatternEvent]:
        ...


# Telemetry helper — used by every pattern. Kept here to avoid circular imports.
def make_root_span(name: str, profile: dict[str, Any], thread_id: str | None) -> Any:
    """Start the root OTel span for a pattern run.

    Attrs match research doc §Observability posture:
        pattern, ls.tier, ls.provider, ls.thread.id
    """
    from localsmartz.observability import get_tracer

    tracer = get_tracer("localsmartz.patterns")
    span_cm = tracer.start_as_current_span("ls.pattern.run")
    # We'll return the CM so caller can enter/exit; set attrs after enter.
    return span_cm, {
        "pattern": name,
        "ls.tier": profile.get("tier", profile.get("name", "unknown")),
        "ls.provider": profile.get("provider", "ollama"),
        "ls.thread.id": thread_id or "",
    }
