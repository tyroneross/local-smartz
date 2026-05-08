"""Streaming coverage for chain + router + orchestrator (commit D, 2026-05-08).

Extends the c6 streaming primitive (``patterns/single.run`` consumes
``runner.stream_turn``) to the three sequential patterns where token-level
streaming is unambiguous: each role takes its turn, then yields control.

Covered patterns:
  - chain.py        — three stages emit text_delta with role=<stage>.
  - router.py       — specialist turn streams (router classification turn
                       does NOT — one-word output).
  - orchestrator.py — planner + synthesizer stream; workers stay
                       non-streamed (concurrent fan-out).

Patterns NOT covered here (phase-boundary events instead, see commit E):
  critic_loop.py, parallel.py, reflection.py.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import pytest


def _drain(coro_iter: Any) -> list[dict]:
    async def _go() -> list[dict]:
        out: list[dict] = []
        async for ev in coro_iter:
            out.append(ev)
        return out

    return asyncio.run(_go())


class _StreamingScriptRunner:
    """stream_turn yields scripted deltas per call.

    ``scripts`` is a list of delta-lists; one entry per call. Each call
    consumes one entry.
    """

    def __init__(self, scripts: list[list[str]]) -> None:
        self.scripts = list(scripts)
        self.calls = 0
        self.run_turn_calls = 0

    def _next(self) -> list[str]:
        if self.calls >= len(self.scripts):
            raise AssertionError(
                f"_StreamingScriptRunner out of scripts at call {self.calls}"
            )
        deltas = self.scripts[self.calls]
        self.calls += 1
        return deltas

    async def run_turn(
        self,
        prompt: str,
        *,
        tools: Any = None,
        model_ref: dict,
        system: str | None = None,
        ctx: dict | None = None,
    ) -> dict:
        self.run_turn_calls += 1
        deltas = self._next()
        body = "".join(deltas)
        return {
            "content": body,
            "tool_calls": [],
            "usage": {"input_tokens": 5, "output_tokens": 5},
        }

    async def stream_turn(
        self,
        prompt: str,
        *,
        tools: Any = None,
        model_ref: dict,
        system: str | None = None,
        ctx: dict | None = None,
    ) -> AsyncIterator[dict]:
        deltas = self._next()
        for d in deltas:
            yield {"delta": d, "done": False}
        yield {
            "delta": "",
            "done": True,
            "final": {
                "content": "".join(deltas),
                "tool_calls": [],
                "usage": {"input_tokens": 5, "output_tokens": 5},
                "model": model_ref.get("name", ""),
                "provider": model_ref.get("provider", ""),
            },
        }


# --- chain ---------------------------------------------------------------

def test_chain_streams_text_delta_per_stage(monkeypatch):
    """chain.run must emit text_delta events with role=<stage_name> for
    each of the three default stages (gather, analyze, write)."""
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)
    from localsmartz.patterns import chain

    runner = _StreamingScriptRunner([
        ["gather", " ", "notes"],
        ["analyze", " ", "findings"],
        ["write", " ", "answer"],
    ])
    agents = {
        "primary": {"model_ref": {"provider": "groq", "name": "llama-3.1-8b-instant"}}
    }
    profile = {"name": "full"}

    events = _drain(
        chain.run("question", agents=agents, profile=profile, stream=True, runner=runner)
    )

    deltas = [(e.get("role"), e.get("delta")) for e in events if e.get("type") == "text_delta"]
    assert deltas == [
        ("gather", "gather"), ("gather", " "), ("gather", "notes"),
        ("analyze", "analyze"), ("analyze", " "), ("analyze", "findings"),
        ("write", "write"), ("write", " "), ("write", "answer"),
    ]
    # And exactly three turn events (one per stage), in the order the
    # stages run.
    turn_roles = [e.get("role") for e in events if e.get("type") == "turn"]
    assert turn_roles == ["gather", "analyze", "write"]
    # stream_turn used 3x, run_turn never.
    assert runner.calls == 3
    assert runner.run_turn_calls == 0


# --- router --------------------------------------------------------------

def test_router_streams_specialist_only(monkeypatch):
    """router.run streams the specialist turn but NOT the router
    classification turn (one-word output, streaming would add noise)."""
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)
    from localsmartz.patterns import router

    # Router stays UN-streamed (uses run_turn) — that's the existing
    # behavior; we don't enforce streaming on the router turn. To exercise
    # this we need a runner that ALSO has a working run_turn. The
    # _StreamingScriptRunner does. Both router and specialist turns will
    # be served from the script — but the pattern only calls stream_turn
    # for the specialist.
    runner = _StreamingScriptRunner([
        ["technical"],          # router classification (full body via run_turn)
        ["spec ", "answer"],   # specialist (streamed via stream_turn)
    ])

    agents = {
        "router": {"model_ref": {"provider": "groq", "name": "llama-3.1-8b-instant"}},
        "specialist": {"model_ref": {"provider": "groq", "name": "llama-3.1-8b-instant"}},
    }
    profile = {"name": "full"}
    ctx = {
        "specialists": [
            {"label": "technical"},
            {"label": "general"},
        ]
    }

    events = _drain(
        router.run("Q", agents=agents, profile=profile, stream=True, runner=runner, ctx=ctx)
    )

    # Specialist deltas surface; router classification does not.
    deltas = [(e.get("role"), e.get("delta")) for e in events if e.get("type") == "text_delta"]
    assert deltas == [
        ("specialist:technical", "spec "),
        ("specialist:technical", "answer"),
    ]


# --- orchestrator --------------------------------------------------------

_DECOMPOSITION_JSON = (
    '[{"role":"alpha","prompt":"sub-task A"},'
    '{"role":"beta","prompt":"sub-task B"}]'
)


def test_orchestrator_streams_planner_and_synth(monkeypatch):
    """orchestrator.run streams planner (role=orchestrator.plan) + synth
    (role=final). Workers stay non-streamed (concurrent fan-out — token
    interleaving across workers would be unreadable)."""
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)
    from localsmartz.patterns import orchestrator

    runner = _StreamingScriptRunner([
        # Planner — JSON in deltas.
        list(_DECOMPOSITION_JSON),
        # Worker alpha — uses run_turn (workers always do)
        ["alpha-output"],
        # Worker beta
        ["beta-output"],
        # Synthesizer
        ["final ", "synth ", "answer"],
    ])
    agents = {
        "orchestrator": {
            "model_ref": {"provider": "anthropic", "name": "claude-haiku-4"}
        },
        "worker": {
            "model_ref": {"provider": "anthropic", "name": "claude-haiku-4"}
        },
    }
    profile = {"name": "full", "tier": "full", "provider": "anthropic"}

    events = _drain(
        orchestrator.run(
            "ten-token prompt",
            agents=agents,
            profile=profile,
            stream=True,
            runner=runner,
        )
    )

    # Planner streamed.
    planner_roles = {
        e.get("role") for e in events if e.get("type") == "text_delta"
    }
    assert "orchestrator.plan" in planner_roles
    # Synth streamed under role="final".
    assert "final" in planner_roles
    # And the synth deltas are exactly what we scripted, in order.
    final_deltas = [
        e["delta"] for e in events
        if e.get("type") == "text_delta" and e.get("role") == "final"
    ]
    assert final_deltas == ["final ", "synth ", "answer"]
    # Workers' run_turn was used (not stream_turn) — verified indirectly:
    # orchestrator workers don't yield text_delta events.
    worker_deltas = [
        e for e in events
        if e.get("type") == "text_delta"
        and e.get("role", "").startswith("worker")
    ]
    assert worker_deltas == []
    # And exactly two worker turn events (one per task) survived.
    worker_turns = [
        e for e in events
        if e.get("type") == "turn" and e.get("role", "").startswith("worker.")
    ]
    assert len(worker_turns) == 2


def test_orchestrator_falls_back_to_run_turn_when_no_stream(monkeypatch):
    """stream=False on orchestrator.run must use run_turn for planner +
    synth too — no text_delta events emitted."""
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)
    from localsmartz.patterns import orchestrator

    runner = _StreamingScriptRunner([
        list(_DECOMPOSITION_JSON),
        ["alpha"],
        ["beta"],
        ["final"],
    ])
    agents = {
        "orchestrator": {"model_ref": {"provider": "ollama", "name": "qwen3:8b"}},
        "worker": {"model_ref": {"provider": "ollama", "name": "qwen3:8b"}},
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
    assert [e for e in events if e.get("type") == "text_delta"] == []
    # All four calls went through run_turn.
    assert runner.run_turn_calls == 4
