"""Pattern protocol + shared event shapes.

Every pattern is an ``async def run(...)`` that yields ``PatternEvent`` dicts.
Callers (HTTP SSE, CLI) stream the events; the final event is always
``{"type": "done", ...}``.
"""
from __future__ import annotations

from typing import Any, AsyncIterator, Awaitable, Callable, Protocol, TypedDict

from localsmartz.runners import AgentRunner


class PatternEvent(TypedDict, total=False):
    # type taxonomy:
    #   "pattern_start" | "turn" | "tool" | "iteration" | "score" | "done"
    #   "budget_warn"  — cloud-only token-budget advisory (feat: c8 follow-up)
    #   "text_delta"   — token-level streaming chunk (feat: c6 follow-up)
    #   "phase_start" / "phase_end"  — sub-phase boundaries inside a pattern
    #     (commit E, 2026-05-08). Used by patterns where token-level
    #     interleaving across roles is ambiguous (critic_loop / parallel /
    #     reflection); preferred over text_delta when concurrent roles
    #     would muddle the stream.
    type: str
    pattern: str
    role: str
    content: str
    delta: str
    tool_calls: list[dict[str, Any]]
    iteration: int
    score: float
    verdict: str
    thread_id: str
    duration_ms: int
    # phase_start / phase_end fields
    phase: str
    # budget_warn fields
    session_tokens: int
    threshold: int
    advisory: bool
    provider: str


class BudgetTracker:
    """Per-run token accumulator + one-shot ``budget_warn`` emitter.

    Patterns instantiate one tracker per ``run()``. After every cloud
    ``run_turn``, call ``tracker.tick(usage, provider)``. The tracker
    accumulates ``input_tokens + output_tokens`` and returns a
    ``budget_warn`` event the first (and only the first) time the running
    total crosses the configured threshold. Subsequent ticks past the
    threshold return None — the warn fires once per session.

    Cloud-only: ``provider == "ollama"`` always returns None regardless of
    accumulated tokens (preserved from ``serve._emit_budget_warn``).

    Threshold defaults to ``serve._read_budget_threshold()`` (reads
    ``.localsmartz/config.json.budget_tokens_warn``); pass an explicit
    threshold to bypass the file read in tests.
    """

    def __init__(self, threshold: int | None = None) -> None:
        if threshold is None:
            try:
                from localsmartz.serve import _read_budget_threshold

                threshold = _read_budget_threshold()
            except Exception:  # noqa: BLE001 — never block a pattern
                threshold = None
        self._threshold = threshold
        self._session_tokens = 0
        self._warned = False

    @property
    def session_tokens(self) -> int:
        return self._session_tokens

    @property
    def threshold(self) -> int | None:
        return self._threshold

    def tick(self, usage: dict[str, Any] | None, provider: str) -> PatternEvent | None:
        """Accumulate tokens; return a ``budget_warn`` event the first time
        the threshold is crossed, else None.
        """
        if not isinstance(usage, dict):
            return None
        try:
            self._session_tokens += int(usage.get("input_tokens", 0) or 0)
            self._session_tokens += int(usage.get("output_tokens", 0) or 0)
        except (TypeError, ValueError):
            return None
        if self._warned:
            return None
        try:
            from localsmartz.serve import _emit_budget_warn

            event = _emit_budget_warn(
                session_tokens=self._session_tokens,
                threshold=self._threshold,
                provider=provider,
            )
        except Exception:  # noqa: BLE001
            return None
        if event is None:
            return None
        self._warned = True
        return {**event}  # shallow copy; treat as PatternEvent


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


async def stream_or_run_turn(
    runner: AgentRunner,
    prompt: str,
    *,
    role: str,
    tools: Any = None,
    model_ref: dict,
    system: str | None = None,
    ctx: dict | None = None,
    stream: bool = True,
) -> AsyncIterator[PatternEvent | dict]:
    """Drive one turn through ``runner`` and yield streaming events + a
    final terminal envelope (commit D, 2026-05-08).

    Yields:
      - one ``text_delta`` PatternEvent per token chunk WHEN streaming is
        enabled AND the runner exposes ``stream_turn``.
      - one final dict ``{"_final": True, "turn": <AssistantTurn>}`` —
        NOT a PatternEvent; the caller unwraps it to compose the
        pattern-specific terminal events (``turn``, ``budget_warn``, etc.).

    Falls back to ``run_turn`` (whole-turn flush) when:
      - ``stream`` is False, OR
      - the runner doesn't expose ``stream_turn`` (local_ollama / harmony).

    Replicates the assemble + error + fallback logic single.py already
    encodes so chain / router / orchestrator can ship streaming without
    duplicating it.

    Error handling: a stream-side error surfaces as a final turn payload
    with ``content="[error] <msg>"`` so the caller's downstream events
    stay deterministic.
    """
    use_streaming = stream and hasattr(runner, "stream_turn")
    if use_streaming:
        assembled = ""
        final_turn: dict = {}
        async for chunk in runner.stream_turn(  # type: ignore[attr-defined]
            prompt,
            tools=tools,
            model_ref=model_ref,
            system=system,
            ctx=ctx,
        ):
            if chunk.get("done"):
                final_turn = chunk.get("final") or {}
                if chunk.get("error"):
                    final_turn = {
                        "content": f"[error] {chunk.get('error')}",
                        "tool_calls": [],
                        "usage": {},
                    }
                break
            delta = chunk.get("delta", "")
            if delta:
                assembled += delta
                yield {
                    "type": "text_delta",
                    "role": role,
                    "delta": delta,
                }
        turn = final_turn or {
            "content": assembled,
            "tool_calls": [],
            "usage": {},
        }
        yield {"_final": True, "turn": turn}
        return

    turn = await runner.run_turn(
        prompt,
        tools=tools,
        model_ref=model_ref,
        system=system,
        ctx=ctx,
    )
    yield {"_final": True, "turn": turn}


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
