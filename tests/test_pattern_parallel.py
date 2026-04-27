"""Tests for patterns/parallel.py (Phase 3 Pattern C).

Locks in:
- Span tree: ls.pattern.run[pattern=parallel] → ls.sample.<n> + ls.synthesize.
- P4 guard: temperature < 0.3 raises ParallelTemperatureError.
- Mini tier semaphore=1 (no concurrent samples).
- Synthesizer default by tier (mini → same_model, standard → stronger_model).
"""
from __future__ import annotations

import asyncio

import pytest


class _ConcurrencyProbeRunner:
    """Async-aware stub that records concurrent in-flight call count.

    Each run_turn sleeps briefly so the event loop overlaps concurrent
    calls when allowed; otherwise the semaphore serializes them.
    """

    def __init__(self, scripts, delay=0.02):
        self.scripts = list(scripts)
        self.calls = 0
        self.delay = delay
        self.in_flight = 0
        self.peak_in_flight = 0
        self.history: list[dict] = []

    async def run_turn(self, prompt, *, model_ref=None, tools=None, system=None, ctx=None):
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            if self.calls >= len(self.scripts):
                raise AssertionError(
                    f"runner out of scripts (calls={self.calls})"
                )
            self.history.append({
                "prompt": prompt,
                "model_ref": model_ref,
                "ctx_temperature": (ctx or {}).get("temperature"),
            })
            content = self.scripts[self.calls]
            self.calls += 1
            return {"content": content}
        finally:
            self.in_flight -= 1


def _install_in_memory_tracing():
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
    async def _r():
        out = []
        async for ev in coro_iter:
            out.append(ev)
        return out
    return asyncio.run(_r())


def test_parallel_span_shape():
    """Root + 3 sample children + synthesize. All required attrs."""
    from localsmartz.patterns import parallel

    exporter = _install_in_memory_tracing()

    runner = _ConcurrencyProbeRunner(["s1", "s2", "s3", "FINAL"])
    profile = {"tier": "standard", "provider": "ollama", "budget": {"parallel_n": 3}}
    agents = {
        "sampler": {"model_ref": {"provider": "ollama", "name": "qwen3.5:9b"}},
    }

    events = _drain(parallel.run(
        "ping", agents=agents, profile=profile, runner=runner,
        ctx={"thread_id": "th-par-1"},
    ))

    spans = exporter.get_finished_spans()
    roots = [s for s in spans if s.name == "ls.pattern.run"]
    assert roots
    root = roots[-1]
    attrs = dict(root.attributes)
    assert attrs.get("pattern") == "parallel"
    assert attrs.get("ls.thread.id") == "th-par-1"
    assert attrs.get("ls.parallel.n") == 3

    sample_spans = [s for s in spans if s.name.startswith("ls.sample.")]
    assert len(sample_spans) == 3
    for ss in sample_spans:
        for req in ("ls.sample.index", "ls.sample.temperature", "ls.sample.duration_ms", "ls.sample.status"):
            assert req in ss.attributes, (
                f"{ss.name} missing {req}; got {dict(ss.attributes)}"
            )

    assert any(s.name == "ls.synthesize" for s in spans)

    types = [e.get("type") for e in events]
    assert types[0] == "pattern_start"
    assert types[-1] == "done"
    final = next(e for e in events if e.get("role") == "final")
    assert final.get("content") == "FINAL"


def test_parallel_min_temperature_enforced():
    """P4 guard: temperature below 0.3 raises ParallelTemperatureError."""
    from localsmartz.patterns import parallel

    _install_in_memory_tracing()
    runner = _ConcurrencyProbeRunner([])
    profile = {
        "tier": "standard",
        "provider": "ollama",
        "budget": {"parallel_n": 3},
        "parallel": {"temperatures": [0.1, 0.5, 0.7]},  # 0.1 violates guard
    }
    agents = {"sampler": {"model_ref": {"provider": "ollama", "name": "x"}}}

    with pytest.raises(parallel.ParallelTemperatureError):
        _drain(parallel.run(
            "ping", agents=agents, profile=profile, runner=runner, ctx={}
        ))


def test_parallel_semaphore_limits_concurrency_mini():
    """Mini tier: semaphore=1 → never more than 1 in-flight sample."""
    from localsmartz.patterns import parallel

    _install_in_memory_tracing()

    runner = _ConcurrencyProbeRunner(["s1", "s2", "s3", "final"], delay=0.05)
    profile = {
        "tier": "mini",
        "provider": "ollama",
        "budget": {"parallel_n": 3},
    }
    agents = {"sampler": {"model_ref": {"provider": "ollama", "name": "qwen3.5:9b"}}}

    _drain(parallel.run(
        "ping", agents=agents, profile=profile, runner=runner, ctx={}
    ))

    # Peak in-flight must never exceed 1 on mini — includes the final
    # synthesize call but that runs after samples are drained.
    assert runner.peak_in_flight == 1, (
        f"mini semaphore should keep peak in-flight at 1; got {runner.peak_in_flight}"
    )


def test_parallel_semaphore_standard_allows_2():
    """Standard tier: semaphore=2 → up to 2 concurrent samples."""
    from localsmartz.patterns import parallel

    _install_in_memory_tracing()

    runner = _ConcurrencyProbeRunner(["s1", "s2", "s3", "final"], delay=0.05)
    profile = {
        "tier": "standard",
        "provider": "ollama",
        "budget": {"parallel_n": 3},
    }
    agents = {"sampler": {"model_ref": {"provider": "ollama", "name": "qwen3.5:9b"}}}

    _drain(parallel.run(
        "ping", agents=agents, profile=profile, runner=runner, ctx={}
    ))

    assert runner.peak_in_flight >= 2, (
        f"standard semaphore should allow >=2 concurrent; peak={runner.peak_in_flight}"
    )
    # But never more than 2 during the sample phase.
    assert runner.peak_in_flight <= 2, (
        f"standard semaphore must cap at 2; peak={runner.peak_in_flight}"
    )


def test_parallel_synthesizer_default_mini_same_model():
    """Mini tier default: synthesizer uses sampler's model (same_model)."""
    from localsmartz.patterns import parallel

    _install_in_memory_tracing()

    runner = _ConcurrencyProbeRunner(["s1", "s2", "s3", "final"])
    sampler_ref = {"provider": "ollama", "name": "qwen3.5:9b"}
    synth_ref = {"provider": "ollama", "name": "qwen3.5:27b"}  # bigger

    profile = {
        "tier": "mini",
        "provider": "ollama",
        "budget": {"parallel_n": 3},
    }
    agents = {
        "sampler": {"model_ref": sampler_ref},
        # Even if a synthesizer slot is present, mini default ignores it.
        "synthesizer": {"model_ref": synth_ref},
    }

    _drain(parallel.run(
        "ping", agents=agents, profile=profile, runner=runner, ctx={}
    ))

    # Last call is synthesize — must have used sampler_ref.
    synth_call = runner.history[-1]
    assert synth_call["model_ref"] == sampler_ref, (
        f"mini tier must default synthesizer to sampler model; "
        f"got {synth_call['model_ref']}"
    )


def test_parallel_synthesizer_default_standard_stronger_model():
    """Standard tier default: synthesizer uses synthesizer slot (stronger_model)."""
    from localsmartz.patterns import parallel

    _install_in_memory_tracing()

    runner = _ConcurrencyProbeRunner(["s1", "s2", "s3", "final"])
    sampler_ref = {"provider": "ollama", "name": "qwen3.5:9b"}
    synth_ref = {"provider": "ollama", "name": "qwen3.5:27b"}

    profile = {
        "tier": "standard",
        "provider": "ollama",
        "budget": {"parallel_n": 3},
    }
    agents = {
        "sampler": {"model_ref": sampler_ref},
        "synthesizer": {"model_ref": synth_ref},
    }

    _drain(parallel.run(
        "ping", agents=agents, profile=profile, runner=runner, ctx={}
    ))

    synth_call = runner.history[-1]
    assert synth_call["model_ref"] == synth_ref, (
        f"standard tier should use synthesizer slot model; "
        f"got {synth_call['model_ref']}"
    )


def test_parallel_temperatures_propagate_to_ctx():
    """Each sample call receives ctx['temperature'] = its scheduled temperature."""
    from localsmartz.patterns import parallel

    _install_in_memory_tracing()

    runner = _ConcurrencyProbeRunner(["s1", "s2", "s3", "final"])
    profile = {
        "tier": "standard",
        "provider": "ollama",
        "budget": {"parallel_n": 3},
        "parallel": {"temperatures": [0.3, 0.5, 0.7]},
    }
    agents = {"sampler": {"model_ref": {"provider": "ollama", "name": "x"}}}

    _drain(parallel.run(
        "ping", agents=agents, profile=profile, runner=runner, ctx={}
    ))

    # Sample calls are the first 3 history entries. Order is non-deterministic
    # under asyncio.gather but the set of temperatures seen must match.
    sample_temps = sorted(
        h["ctx_temperature"] for h in runner.history[:3]
    )
    assert sample_temps == [0.3, 0.5, 0.7], (
        f"each sample must carry its temperature in ctx; got {sample_temps}"
    )
