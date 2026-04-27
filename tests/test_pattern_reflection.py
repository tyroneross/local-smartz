"""Tests for patterns/reflection.py (Phase 3 Pattern A).

Locks in:
- Span tree: ls.pattern.run[pattern=reflection] → ls.reflection.<n> children
  with ls.reflection.{index, rubric_score, revision_reason}.
- Stagnation abort (P3 guard): flat scores → verdict=stagnant, early exit.
- Budget exhaustion (F5 guard): max_iterations hard cap at 3.
- threads.append_entry called with kind='reflection'.
- Mini-tier F2 colocation log.
"""
from __future__ import annotations

import asyncio
import tempfile

import pytest

from localsmartz.threads import create_thread


class _ScriptedRunner:
    """Minimal AgentRunner — plays back scripted (primary, reflector) pairs.

    Each call alternates primary → reflector → primary → reflector …
    The scripts parameter lists the content returned for each call in
    order. Construct with a list of strings.
    """

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = 0

    async def run_turn(self, prompt, *, model_ref=None, tools=None, system=None, ctx=None):
        if self.calls >= len(self.scripts):
            raise AssertionError(
                f"runner out of scripts (calls={self.calls})"
            )
        content = self.scripts[self.calls]
        self.calls += 1
        return {"content": content}


def _install_in_memory_tracing():
    """Attach-aware installer (OTel forbids overriding a set provider)."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    current = trace.get_tracer_provider()
    exporter = InMemorySpanExporter()
    if isinstance(current, TracerProvider):
        current.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
    return exporter


def _drain(coro_iter):
    async def _run():
        out = []
        async for ev in coro_iter:
            out.append(ev)
        return out

    return asyncio.run(_run())


def test_reflection_span_shape():
    """Lock in ls.pattern.run + ls.reflection.* tree with required attrs."""
    from localsmartz.patterns import reflection

    exporter = _install_in_memory_tracing()

    # Iter 1: primary draft, reflector scores 0.5 (revise).
    # Iter 2: primary revised draft, reflector scores 0.9 (pass).
    runner = _ScriptedRunner([
        "draft 1 content",
        '{"score": 0.5, "revision_reason": "thin", "suggest": "add numbers"}',
        "draft 2 content with numbers",
        '{"score": 0.9, "revision_reason": "strong", "suggest": ""}',
    ])

    profile = {"tier": "standard", "provider": "ollama", "budget": {}}
    agents = {
        "primary": {
            "model_ref": {"provider": "ollama", "name": "qwen3.5:9b"},
            "system_focus": "test-primary",
        },
        "reflector": {
            "model_ref": {"provider": "ollama", "name": "qwen3.5:9b"},
            "system_focus": "test-reflector",
        },
    }

    events = _drain(reflection.run(
        "what is 2+2",
        agents=agents,
        profile=profile,
        runner=runner,
        ctx={"thread_id": "th-refl-1"},
    ))

    spans = exporter.get_finished_spans()
    by_name = [s.name for s in spans]

    # Root span must exist with required attrs.
    roots = [s for s in spans if s.name == "ls.pattern.run"]
    assert roots, f"missing ls.pattern.run; got {by_name}"
    root = roots[-1]  # latest root emitted by this test
    attrs = dict(root.attributes)
    assert attrs.get("pattern") == "reflection"
    assert attrs.get("ls.tier") == "standard"
    assert attrs.get("ls.provider") == "ollama"
    assert attrs.get("ls.thread.id") == "th-refl-1"
    assert "ls.reflection.final_score" in attrs

    # Two iteration children expected — the stub forces 2 iterations.
    iter_spans = [s for s in spans if s.name.startswith("ls.reflection.")]
    assert len(iter_spans) == 2, (
        f"expected 2 reflection spans; got {len(iter_spans)}"
    )
    for it in iter_spans:
        for required in ("ls.reflection.index", "ls.reflection.rubric_score", "ls.reflection.revision_reason"):
            assert required in it.attributes, (
                f"{it.name} missing {required}; got {dict(it.attributes)}"
            )

    # Events in the contract order.
    types = [e.get("type") for e in events]
    assert types[0] == "pattern_start"
    assert types[-1] == "done"
    assert "iteration" in types
    assert "turn" in types

    # Final verdict must be pass.
    final = next(e for e in events if e.get("role") == "final")
    assert final.get("verdict") == "pass"
    assert final.get("score") == pytest.approx(0.9)


def test_reflection_stagnation_aborts():
    """P3 guard: flat scores between iterations abort with verdict=stagnant."""
    from localsmartz.patterns import reflection

    _install_in_memory_tracing()

    # Iter 1: score 0.6. Iter 2: score 0.62 (delta 0.02 < min_delta 0.05) → stagnant.
    runner = _ScriptedRunner([
        "draft 1",
        '{"score": 0.6, "revision_reason": "needs more", "suggest": "add more"}',
        "draft 2",
        '{"score": 0.62, "revision_reason": "still thin", "suggest": "try again"}',
    ])

    profile = {
        "tier": "standard",
        "provider": "ollama",
        "budget": {"min_reflection_delta": 0.05},
    }
    agents = {
        "primary": {"model_ref": {"provider": "ollama", "name": "qwen3.5:9b"}},
        "reflector": {"model_ref": {"provider": "ollama", "name": "qwen3.5:9b"}},
    }

    events = _drain(reflection.run(
        "ping",
        agents=agents,
        profile=profile,
        runner=runner,
        ctx={},
    ))

    final = next(e for e in events if e.get("role") == "final")
    assert final.get("verdict") == "stagnant", (
        f"expected stagnant verdict; got {final!r}"
    )


def test_reflection_budget_exhaustion_hard_caps_at_3():
    """F5 guard: max_iterations cannot exceed 3 even when profile asks more."""
    from localsmartz.patterns import reflection

    _install_in_memory_tracing()

    # Steadily improving scores but never reaching pass_threshold (0.85).
    # Scripts for 3 full iterations (2 calls each = 6 calls).
    runner = _ScriptedRunner([
        "d1", '{"score": 0.40, "revision_reason": "a", "suggest": "a"}',
        "d2", '{"score": 0.50, "revision_reason": "b", "suggest": "b"}',
        "d3", '{"score": 0.60, "revision_reason": "c", "suggest": "c"}',
        # If 4th iter were to happen the stub would raise — we rely on
        # that as a negative assertion: max_iter MUST be 3.
    ])

    profile = {
        "tier": "standard",
        "provider": "ollama",
        # Try to smuggle in max_iterations=10 — the pattern must clamp to 3.
        "budget": {"max_iterations": 10, "max_turns": 20},
    }
    agents = {
        "primary": {"model_ref": {"provider": "ollama", "name": "qwen3.5:9b"}},
        "reflector": {"model_ref": {"provider": "ollama", "name": "qwen3.5:9b"}},
    }

    events = _drain(reflection.run(
        "ping", agents=agents, profile=profile, runner=runner, ctx={}
    ))

    # Exactly 3 iterations — and the runner is fully drained (6 calls).
    iterations = [e for e in events if e.get("type") == "iteration"]
    assert len(iterations) == 3, (
        f"expected exactly 3 iterations (hard cap F5); got {len(iterations)}"
    )
    assert runner.calls == 6


def test_reflection_threads_append_entry_kind(tmp_path, monkeypatch):
    """Each iteration writes append_entry(kind='reflection')."""
    from localsmartz.patterns import reflection

    _install_in_memory_tracing()

    # Create a real thread directory so append_entry doesn't raise.
    cwd = str(tmp_path)
    create_thread("th-kind", cwd, "Reflection kind test")

    runner = _ScriptedRunner([
        "d1", '{"score": 0.4, "revision_reason": "r1", "suggest": "s1"}',
        "d2", '{"score": 0.9, "revision_reason": "r2", "suggest": ""}',
    ])

    profile = {"tier": "standard", "provider": "ollama", "budget": {}}
    agents = {
        "primary": {"model_ref": {"provider": "ollama", "name": "qwen3.5:9b"}},
        "reflector": {"model_ref": {"provider": "ollama", "name": "qwen3.5:9b"}},
    }

    _drain(reflection.run(
        "ping",
        agents=agents,
        profile=profile,
        runner=runner,
        ctx={"thread_id": "th-kind", "cwd": cwd},
    ))

    # Read the persisted JSONL and assert each record has kind=reflection.
    messages = tmp_path / ".localsmartz" / "threads" / "th-kind" / "messages.jsonl"
    lines = messages.read_text().strip().splitlines()
    assert len(lines) == 2, (
        f"expected 2 reflection entries; got {len(lines)}"
    )
    import json
    for line in lines:
        record = json.loads(line)
        assert record.get("kind") == "reflection", (
            f"expected kind=reflection; got {record!r}"
        )


def test_reflection_mini_tier_forces_shared_model(caplog):
    """F2 guard: on mini tier, reflector forced to share primary's model."""
    from localsmartz.patterns import reflection

    _install_in_memory_tracing()

    runner = _ScriptedRunner([
        "d1", '{"score": 0.9, "revision_reason": "good", "suggest": ""}',
    ])

    profile = {"tier": "mini", "provider": "ollama", "budget": {}}
    agents = {
        "primary": {"model_ref": {"provider": "ollama", "name": "qwen3.5:9b"}},
        "reflector": {"model_ref": {"provider": "ollama", "name": "qwen3.5:27b"}},
    }

    import logging
    caplog.set_level(logging.INFO, logger="localsmartz.patterns.reflection")

    _drain(reflection.run(
        "ping", agents=agents, profile=profile, runner=runner, ctx={}
    ))

    assert any(
        "forcing reflector.model_ref=primary (F2)" in r.message
        for r in caplog.records
    ), "F2 colocation log missing"
