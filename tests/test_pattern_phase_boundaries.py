"""Phase-boundary events for critic_loop + parallel + reflection
(commit E, 2026-05-08).

Where token-level interleaving across roles is genuinely ambiguous (multiple
roles streaming concurrently or interleaved across iterations), patterns
ship phase markers + full-text events instead of text_delta.

Schema:
    {"type": "phase_start", "phase": "<name>", "iteration"?: N}
    {"type": "phase_end",   "phase": "<name>", "iteration"?: N}

The full-text ``turn`` event per role still fires inside each phase —
phase events are companions, not replacements. SSE consumers can render
loop transitions cleanly without trying to attribute interleaved tokens.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest


def _drain(coro_iter: Any) -> list[dict]:
    async def _go() -> list[dict]:
        out: list[dict] = []
        async for ev in coro_iter:
            out.append(ev)
        return out

    return asyncio.run(_go())


class _ScriptedRunner:
    """Runs scripts for run_turn; deliberately does NOT expose stream_turn
    so the patterns under test stay on the canonical run_turn path."""

    def __init__(self, scripts: list[str]) -> None:
        self.scripts = list(scripts)
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
        if self.calls >= len(self.scripts):
            raise AssertionError(
                f"runner out of scripts at call {self.calls}; "
                f"last prompt={prompt[:80]!r}"
            )
        content = self.scripts[self.calls]
        self.calls += 1
        return {
            "content": content,
            "tool_calls": [],
            "usage": {"input_tokens": 5, "output_tokens": 5},
        }


# --- critic_loop ---------------------------------------------------------

def test_critic_loop_emits_phase_pairs_per_iteration(monkeypatch):
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)
    from localsmartz.patterns import critic_loop

    runner = _ScriptedRunner([
        "writer draft 1",                                   # writer iter 1
        '{"score": 0.99, "verdict": "pass", "feedback": "good"}',  # critic iter 1
    ])
    agents = {
        "writer": {"model_ref": {"provider": "groq", "name": "x"}},
        "critic": {"model_ref": {"provider": "groq", "name": "x"}},
    }
    profile = {"name": "full", "tier": "full", "provider": "groq"}

    events = _drain(
        critic_loop.run("Q", agents=agents, profile=profile, stream=True, runner=runner)
    )

    # Expect for each iteration:
    #   phase_start writer, turn writer, phase_end writer,
    #   phase_start critic, iteration, phase_end critic
    starts = [(e.get("phase"), e.get("iteration"))
              for e in events if e.get("type") == "phase_start"]
    ends = [(e.get("phase"), e.get("iteration"))
            for e in events if e.get("type") == "phase_end"]
    assert starts == [("writer", 1), ("critic", 1)]
    assert ends == [("writer", 1), ("critic", 1)]


def test_critic_loop_phase_pairs_balanced(monkeypatch):
    """Every phase_start must have a matching phase_end in the same role."""
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)
    from localsmartz.patterns import critic_loop

    runner = _ScriptedRunner([
        "draft 1",
        '{"score": 0.5, "verdict": "revise", "feedback": "fix it"}',
        "draft 2",
        '{"score": 0.99, "verdict": "pass", "feedback": "ok"}',
    ])
    agents = {
        "writer": {"model_ref": {"provider": "groq", "name": "x"}},
        "critic": {"model_ref": {"provider": "groq", "name": "x"}},
    }
    profile = {"name": "full", "tier": "full", "provider": "groq"}

    events = _drain(
        critic_loop.run("Q", agents=agents, profile=profile, stream=True, runner=runner)
    )
    starts = [(e["phase"], e["iteration"]) for e in events if e.get("type") == "phase_start"]
    ends = [(e["phase"], e["iteration"]) for e in events if e.get("type") == "phase_end"]
    assert starts == ends
    # Two iterations × two roles = four pairs.
    assert len(starts) == 4


# --- parallel ------------------------------------------------------------

def test_parallel_emits_phase_per_sample_and_synth(monkeypatch):
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)
    from localsmartz.patterns import parallel

    runner = _ScriptedRunner([
        "sample 1", "sample 2", "sample 3",   # 3 samples
        "synthesized answer",                  # synthesizer
    ])
    agents = {
        "sampler": {"model_ref": {"provider": "groq", "name": "x"}},
        "synthesizer": {"model_ref": {"provider": "groq", "name": "x"}},
    }
    profile = {
        "name": "full",
        "tier": "full",
        "provider": "groq",
        "budget": {"parallel_n": 3, "parallel_concurrency": 1},
    }

    events = _drain(
        parallel.run("Q", agents=agents, profile=profile, stream=True, runner=runner)
    )
    phase_events = [e for e in events if e.get("type") in ("phase_start", "phase_end")]
    phases = [(e["type"], e["phase"]) for e in phase_events]
    # Three sample pairs + one synth pair, in order.
    assert phases == [
        ("phase_start", "sample.1"), ("phase_end", "sample.1"),
        ("phase_start", "sample.2"), ("phase_end", "sample.2"),
        ("phase_start", "sample.3"), ("phase_end", "sample.3"),
        ("phase_start", "synthesize"), ("phase_end", "synthesize"),
    ]


# --- reflection ----------------------------------------------------------

def test_reflection_emits_primary_and_reflector_phase_pairs(monkeypatch):
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)
    from localsmartz.patterns import reflection

    runner = _ScriptedRunner([
        "primary draft 1",                                     # primary iter 1
        '{"score": 0.95, "revision_reason": "good", "suggest": ""}',  # reflector
    ])
    agents = {
        "primary": {"model_ref": {"provider": "anthropic", "name": "claude-haiku-4"}},
        "reflector": {"model_ref": {"provider": "anthropic", "name": "claude-haiku-4"}},
    }
    profile = {"name": "full", "tier": "full", "provider": "anthropic"}

    events = _drain(
        reflection.run(
            "Q",
            agents=agents,
            profile=profile,
            stream=True,
            runner=runner,
            ctx={"threshold": 0.85},
        )
    )
    starts = [(e["phase"], e["iteration"]) for e in events if e.get("type") == "phase_start"]
    ends = [(e["phase"], e["iteration"]) for e in events if e.get("type") == "phase_end"]
    assert starts == [("primary", 1), ("reflector", 1)]
    assert ends == [("primary", 1), ("reflector", 1)]


def test_reflection_phase_pairs_balanced_over_two_iterations(monkeypatch):
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)
    from localsmartz.patterns import reflection

    runner = _ScriptedRunner([
        "primary draft 1",
        '{"score": 0.4, "revision_reason": "low", "suggest": "fix it"}',
        "primary draft 2",
        '{"score": 0.99, "revision_reason": "ok", "suggest": ""}',
    ])
    agents = {
        "primary": {"model_ref": {"provider": "anthropic", "name": "claude-haiku-4"}},
        "reflector": {"model_ref": {"provider": "anthropic", "name": "claude-haiku-4"}},
    }
    # min_delta=0.05; 0.99 - 0.4 = 0.59 ≥ 0.05 so the loop survives the
    # P3 stagnation check. Pass threshold defaults to 0.85, so iter 2
    # passes.
    profile = {"name": "full", "tier": "full", "provider": "anthropic"}

    events = _drain(
        reflection.run("Q", agents=agents, profile=profile, stream=True, runner=runner)
    )
    starts = [(e["phase"], e["iteration"]) for e in events if e.get("type") == "phase_start"]
    ends = [(e["phase"], e["iteration"]) for e in events if e.get("type") == "phase_end"]
    assert starts == ends
    # Two iterations × two roles = four pairs.
    assert len(starts) == 4
