"""Span-shape regression test for critic_loop.

Locks in the exact OTel tree the research doc specifies:

    ls.pattern.run               (root)
        attrs: pattern, ls.tier, ls.provider, ls.thread.id, ls.critic_loop.final_score
        └── ls.iteration.1       (child)
                attrs: ls.iteration.index, ls.iteration.score, ls.iteration.verdict
        └── ls.iteration.2 ...

If anyone refactors pattern instrumentation and drops one of those attrs,
this test catches it. Uses the OTel in-memory exporter so we don't need
Phoenix running.
"""
from __future__ import annotations

import asyncio

import pytest


class _StubRunner:
    """Minimal AgentRunner — returns a scripted writer/critic pair.

    Iteration 1: writer returns a placeholder; critic says ``verdict: revise``
    at 0.4. Iteration 2: writer revises; critic passes at 0.9. This forces
    the loop to emit two ls.iteration.* spans before stopping.
    """

    def __init__(self):
        self.calls = 0

    async def run_turn(self, prompt, *, model_ref=None, tools=None, system=None, ctx=None):
        self.calls += 1
        # Odd calls are writer turns; even calls are critic turns.
        if self.calls % 2 == 1:
            return {"content": f"draft-{self.calls}"}
        # Alternate critic verdicts so we get exactly 2 iterations.
        if self.calls == 2:
            return {
                "content": '{"score": 0.4, "verdict": "revise", "feedback": "be more specific"}'
            }
        return {
            "content": '{"score": 0.9, "verdict": "pass", "feedback": "good"}'
        }


def _install_in_memory_tracing():
    """Attach a fresh in-memory span exporter.

    OTel forbids *overriding* an already-installed global TracerProvider,
    so when another test has already set one we attach an extra
    SimpleSpanProcessor to it instead of trying to replace it. Either way
    this helper returns the exporter for the caller to drain.
    """
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


def test_critic_loop_span_shape():
    """Lock in the ls.pattern.run / ls.iteration.* tree + all required attrs."""
    from localsmartz.patterns import critic_loop

    exporter = _install_in_memory_tracing()
    runner = _StubRunner()

    profile = {
        "tier": "mini",
        "provider": "ollama",
        "budget": {"max_iterations": 3, "max_turns": 8},
    }
    agents = {
        "writer": {
            "model_ref": {"provider": "ollama", "name": "qwen3:8b-q4_K_M"},
            "system_focus": "test-writer",
        },
        "critic": {
            "model_ref": {"provider": "ollama", "name": "qwen3:8b-q4_K_M"},
            "system_focus": "test-critic",
        },
    }

    async def _drain():
        out = []
        async for ev in critic_loop.run(
            "what is 2+2",
            agents=agents,
            profile=profile,
            runner=runner,
            ctx={"thread_id": "th-test-1"},
        ):
            out.append(ev)
        return out

    events = asyncio.run(_drain())

    spans = exporter.get_finished_spans()

    # Children finish before parents, so sort-by-start rather than rely on order.
    by_name = {s.name: s for s in spans}

    assert "ls.pattern.run" in by_name, (
        f"missing root span ls.pattern.run; got {[s.name for s in spans]}"
    )
    root = by_name["ls.pattern.run"]

    # Required root attrs (research doc §Observability posture).
    required_root_attrs = {
        "pattern": "critic_loop",
        "ls.tier": "mini",
        "ls.provider": "ollama",
        "ls.thread.id": "th-test-1",
    }
    for k, v in required_root_attrs.items():
        assert root.attributes.get(k) == v, (
            f"root span missing attr {k}={v}; got {dict(root.attributes)}"
        )
    assert "ls.critic_loop.final_score" in root.attributes, (
        "root span missing ls.critic_loop.final_score"
    )

    # Iteration children.
    iter_spans = [s for s in spans if s.name.startswith("ls.iteration.")]
    assert len(iter_spans) == 2, (
        f"expected 2 iteration spans (stub scripts 2 before pass); got {len(iter_spans)}"
    )
    for it in iter_spans:
        for required in ("ls.iteration.index", "ls.iteration.score", "ls.iteration.verdict"):
            assert required in it.attributes, (
                f"{it.name} missing {required}; got {dict(it.attributes)}"
            )
        assert it.parent is not None and it.parent.span_id == root.context.span_id, (
            f"{it.name} is not a child of ls.pattern.run"
        )

    # Verdict of last iteration must be 'pass' (stub scripts this).
    last_iter = sorted(iter_spans, key=lambda s: s.attributes["ls.iteration.index"])[-1]
    assert last_iter.attributes["ls.iteration.verdict"] == "pass"

    # Pattern emitted events in the contract order.
    event_types = [e.get("type") for e in events]
    assert event_types[0] == "pattern_start"
    assert event_types[-1] == "done"
    assert "iteration" in event_types
    assert "turn" in event_types


def test_critic_loop_mini_tier_forces_shared_model(caplog):
    """F2 guard: on mini tier, writer and critic must end up with same model."""
    from localsmartz.patterns import critic_loop

    _install_in_memory_tracing()  # keep tracer happy
    runner = _StubRunner()

    profile = {"tier": "mini", "provider": "ollama"}
    agents = {
        "writer": {"model_ref": {"provider": "ollama", "name": "qwen3:8b-q4_K_M"}},
        "critic": {"model_ref": {"provider": "ollama", "name": "qwen3.5:27b"}},
    }

    import logging

    caplog.set_level(logging.INFO, logger="localsmartz.patterns.critic_loop")

    async def _drain():
        async for _ in critic_loop.run(
            "ping", agents=agents, profile=profile, runner=runner, ctx={}
        ):
            pass

    asyncio.run(_drain())

    assert any("forcing critic.model_ref=writer (F2)" in r.message for r in caplog.records), (
        "F2 log line missing; critic was not forced to share writer model on mini tier"
    )
