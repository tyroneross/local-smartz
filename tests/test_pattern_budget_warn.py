"""Pattern-layer budget_warn instrumentation tests (commit C, 2026-05-08).

The ``serve._emit_budget_warn`` helper + SSE schema landed in c8 but had no
callers. This test verifies the new ``BudgetTracker`` (added to
``patterns/base.py``) accumulates usage tokens across ``run_turn`` calls and
fires exactly ONE ``budget_warn`` PatternEvent the first time the threshold
is crossed.

Cloud-only: ``provider=="ollama"`` is exempted regardless of token total.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from localsmartz.patterns import single
from localsmartz.patterns.base import BudgetTracker


# --- BudgetTracker unit tests --------------------------------------------


def test_below_threshold_no_event():
    bt = BudgetTracker(threshold=1000)
    assert bt.tick({"input_tokens": 100, "output_tokens": 100}, "anthropic") is None
    assert bt.session_tokens == 200


def test_at_threshold_event_fires_once():
    bt = BudgetTracker(threshold=200)
    # First tick crosses threshold (100+100 == 200)
    out = bt.tick({"input_tokens": 100, "output_tokens": 100}, "groq")
    assert out is not None
    assert out["type"] == "budget_warn"
    assert out["session_tokens"] == 200
    assert out["threshold"] == 200
    assert out["provider"] == "groq"
    assert out["advisory"] is True

    # Second tick must NOT re-fire — once-per-session.
    second = bt.tick({"input_tokens": 50, "output_tokens": 50}, "groq")
    assert second is None
    assert bt.session_tokens == 300


def test_ollama_exempt_from_warn():
    bt = BudgetTracker(threshold=10)
    out = bt.tick({"input_tokens": 1000, "output_tokens": 1000}, "ollama")
    assert out is None
    # Counter still increments — exemption is on the warn, not the count.
    assert bt.session_tokens == 2000


def test_threshold_none_disables():
    bt = BudgetTracker(threshold=None)
    out = bt.tick({"input_tokens": 1_000_000, "output_tokens": 0}, "anthropic")
    assert out is None


def test_no_usage_dict_is_safe():
    bt = BudgetTracker(threshold=100)
    assert bt.tick(None, "anthropic") is None
    assert bt.tick({}, "anthropic") is None
    assert bt.session_tokens == 0


def test_malformed_usage_doesnt_raise():
    bt = BudgetTracker(threshold=100)
    # Non-numeric values should be tolerated, not raise.
    assert bt.tick({"input_tokens": "not-a-number"}, "anthropic") is None


# --- single pattern integration ------------------------------------------


class _FakeRunner:
    """Minimal runner that returns canned usage on each run_turn call."""

    def __init__(self, usage: dict[str, Any]) -> None:
        self._usage = usage
        self.calls = 0

    async def run_turn(
        self,
        prompt: str,
        *,
        tools: Any = None,
        model_ref: dict,
        system: str | None = None,
        ctx: dict | None = None,
    ) -> dict:
        self.calls += 1
        return {
            "content": f"answer to: {prompt}",
            "tool_calls": [],
            "usage": dict(self._usage),
            "model": model_ref.get("name", ""),
            "provider": model_ref.get("provider", ""),
        }


def _drain(coro_iter: Any) -> list[dict]:
    """Drain an async iterator (pattern.run) into a list."""

    async def _go() -> list[dict]:
        out: list[dict] = []
        async for ev in coro_iter:
            out.append(ev)
        return out

    return asyncio.run(_go())


def test_single_pattern_emits_budget_warn(monkeypatch):
    """``patterns.single.run`` MUST yield a budget_warn event when the
    cloud usage crosses the configured threshold.
    """
    # Fake serve._read_budget_threshold so we control the threshold without
    # touching disk.
    monkeypatch.setattr(
        "localsmartz.serve._read_budget_threshold", lambda: 100
    )

    runner = _FakeRunner(usage={"input_tokens": 60, "output_tokens": 60})  # 120 > 100
    agents = {
        "primary": {
            "model_ref": {"provider": "groq", "name": "llama-3.1-8b-instant"},
            "system_focus": "test",
        }
    }
    profile = {"name": "lite", "tier": "mini"}

    events = _drain(
        single.run(
            "hello",
            agents=agents,
            profile=profile,
            stream=False,
            runner=runner,
        )
    )

    types = [ev.get("type") for ev in events]
    assert "pattern_start" in types
    assert "turn" in types
    assert "done" in types
    # The headline assertion — exactly one budget_warn event:
    warns = [ev for ev in events if ev.get("type") == "budget_warn"]
    assert len(warns) == 1
    assert warns[0]["provider"] == "groq"
    assert warns[0]["session_tokens"] == 120
    assert warns[0]["threshold"] == 100


def test_single_pattern_no_warn_when_below(monkeypatch):
    """No event when usage stays below threshold."""
    monkeypatch.setattr(
        "localsmartz.serve._read_budget_threshold", lambda: 1000
    )
    runner = _FakeRunner(usage={"input_tokens": 50, "output_tokens": 50})  # 100 < 1000
    agents = {
        "primary": {
            "model_ref": {"provider": "groq", "name": "llama-3.1-8b-instant"},
        }
    }
    profile = {"name": "lite"}

    events = _drain(
        single.run("hello", agents=agents, profile=profile, stream=False, runner=runner)
    )
    warns = [ev for ev in events if ev.get("type") == "budget_warn"]
    assert warns == []


def test_single_pattern_ollama_never_warns(monkeypatch):
    """Cloud-only filter: Ollama runs never fire budget_warn."""
    monkeypatch.setattr(
        "localsmartz.serve._read_budget_threshold", lambda: 1
    )
    runner = _FakeRunner(usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000})
    agents = {
        "primary": {
            "model_ref": {"provider": "ollama", "name": "qwen3:8b"},
        }
    }
    profile = {"name": "lite"}

    events = _drain(
        single.run("hello", agents=agents, profile=profile, stream=False, runner=runner)
    )
    warns = [ev for ev in events if ev.get("type") == "budget_warn"]
    assert warns == []


def test_single_pattern_threshold_none_no_warn(monkeypatch):
    """Disabled threshold (config.json missing or null) → no events."""
    monkeypatch.setattr(
        "localsmartz.serve._read_budget_threshold", lambda: None
    )
    runner = _FakeRunner(usage={"input_tokens": 100_000, "output_tokens": 100_000})
    agents = {
        "primary": {
            "model_ref": {"provider": "anthropic", "name": "claude-haiku-4"},
        }
    }
    profile = {"name": "full"}

    events = _drain(
        single.run("hello", agents=agents, profile=profile, stream=False, runner=runner)
    )
    warns = [ev for ev in events if ev.get("type") == "budget_warn"]
    assert warns == []


# --- orchestrator pattern integration (commit C, 2026-05-08) -----------------
#
# The c8 follow-up (commit c9592bf) wired BudgetTracker into single.py but
# left orchestrator workers ungauged. Worker-side dispatch in
# _dispatch_workers_path_b._one_worker now propagates `usage` through the
# return shape; run() ticks the tracker once per worker AFTER fan-out so
# the warn fires at most once per session even when multiple workers cross
# the threshold simultaneously.


class _OrchestratorScriptedRunner:
    """Runner that scripts content + usage per call.

    Lead's first call is the planner (decomposition JSON). Workers and
    synthesizer follow. Each call records its prompt so the test can
    assert which-call-fired-which.
    """

    def __init__(self, scripts: list[tuple[str, dict[str, Any]]]) -> None:
        # scripts: list of (content, usage) pairs in call order.
        self.scripts = list(scripts)
        self.calls = 0
        self.history: list[dict] = []

    async def run_turn(
        self,
        prompt: str,
        *,
        tools: Any = None,
        model_ref: dict,
        system: str | None = None,
        ctx: dict | None = None,
    ) -> dict:
        if self.calls >= len(self.scripts):
            raise AssertionError(
                f"orchestrator scripted runner out of scripts at call {self.calls}; "
                f"last prompt: {prompt[:80]!r}"
            )
        content, usage = self.scripts[self.calls]
        self.history.append({
            "prompt": prompt,
            "model_ref": dict(model_ref),
            "system": system,
        })
        self.calls += 1
        return {"content": content, "tool_calls": [], "usage": dict(usage)}


_DECOMPOSITION_JSON = (
    '[{"role":"alpha","prompt":"sub-task A"},'
    '{"role":"beta","prompt":"sub-task B"}]'
)


def test_orchestrator_workers_emit_budget_warn(monkeypatch):
    """Two workers each push 60 tokens; with threshold=200 the SECOND
    worker's tick crosses 240 and fires exactly one budget_warn event.
    """
    monkeypatch.setattr(
        "localsmartz.serve._read_budget_threshold", lambda: 200
    )
    from localsmartz.patterns import orchestrator

    runner = _OrchestratorScriptedRunner([
        # Lead planner — 60 tokens. Tracker session=60 (under 200).
        (_DECOMPOSITION_JSON, {"input_tokens": 30, "output_tokens": 30}),
        # Worker alpha — 80 tokens. Tracker session=140 (under 200) after
        # the worker tick.
        ("alpha output", {"input_tokens": 40, "output_tokens": 40}),
        # Worker beta — 80 tokens. Tracker session=220 → warn fires here.
        ("beta output", {"input_tokens": 40, "output_tokens": 40}),
        # Synthesizer — additional 10 tokens; tracker latched, no re-fire.
        ("final synthesis", {"input_tokens": 5, "output_tokens": 5}),
    ])
    agents = {
        "orchestrator": {
            "model_ref": {"provider": "anthropic", "name": "claude-haiku-4"},
        },
        "worker": {
            "model_ref": {"provider": "anthropic", "name": "claude-haiku-4"},
        },
    }
    profile = {"name": "full", "tier": "full", "provider": "anthropic"}

    events = _drain(
        orchestrator.run(
            "ten-token prompt",
            agents=agents,
            profile=profile,
            stream=False,
            runner=runner,
        )
    )

    warns = [ev for ev in events if ev.get("type") == "budget_warn"]
    # Headline assertion: exactly one warn fired.
    assert len(warns) == 1, f"expected 1 warn, got {len(warns)}: {warns}"
    # And the warn fired AFTER worker-2's usage pushed past 200.
    assert warns[0]["session_tokens"] >= 200
    assert warns[0]["provider"] == "anthropic"
    assert warns[0]["advisory"] is True


def test_orchestrator_workers_ollama_no_warn(monkeypatch):
    """Cloud-only filter still applies through the worker layer — Ollama
    workers never fire budget_warn regardless of token volume."""
    monkeypatch.setattr(
        "localsmartz.serve._read_budget_threshold", lambda: 1
    )
    from localsmartz.patterns import orchestrator

    runner = _OrchestratorScriptedRunner([
        (_DECOMPOSITION_JSON, {"input_tokens": 10_000, "output_tokens": 10_000}),
        ("alpha output", {"input_tokens": 10_000, "output_tokens": 10_000}),
        ("beta output", {"input_tokens": 10_000, "output_tokens": 10_000}),
        ("final synthesis", {"input_tokens": 10_000, "output_tokens": 10_000}),
    ])
    agents = {
        "orchestrator": {
            "model_ref": {"provider": "ollama", "name": "qwen3:8b"},
        },
        "worker": {
            "model_ref": {"provider": "ollama", "name": "qwen3:8b"},
        },
    }
    profile = {"name": "full", "tier": "full", "provider": "ollama"}

    events = _drain(
        orchestrator.run(
            "p",
            agents=agents,
            profile=profile,
            stream=False,
            runner=runner,
        )
    )
    warns = [ev for ev in events if ev.get("type") == "budget_warn"]
    assert warns == []


def test_orchestrator_warn_fires_once_across_workers(monkeypatch):
    """Even when EVERY worker crosses the threshold, the warn is
    emitted exactly once (BudgetTracker._warned latches after first fire)."""
    monkeypatch.setattr(
        "localsmartz.serve._read_budget_threshold", lambda: 50
    )
    from localsmartz.patterns import orchestrator

    big_usage = {"input_tokens": 500, "output_tokens": 500}
    runner = _OrchestratorScriptedRunner([
        # Planner already exceeds threshold by itself — fires the warn.
        (_DECOMPOSITION_JSON, big_usage),
        # Both workers also push past — must not re-fire.
        ("alpha output", big_usage),
        ("beta output", big_usage),
        # Synth — also no re-fire.
        ("final synthesis", big_usage),
    ])
    agents = {
        "orchestrator": {
            "model_ref": {"provider": "groq", "name": "llama-3.1-8b-instant"},
        },
        "worker": {
            "model_ref": {"provider": "groq", "name": "llama-3.1-8b-instant"},
        },
    }
    profile = {"name": "full", "tier": "full", "provider": "groq"}

    events = _drain(
        orchestrator.run(
            "p",
            agents=agents,
            profile=profile,
            stream=False,
            runner=runner,
        )
    )
    warns = [ev for ev in events if ev.get("type") == "budget_warn"]
    assert len(warns) == 1, f"expected exactly 1 warn (latched), got {len(warns)}"
