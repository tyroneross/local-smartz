"""Tests for patterns/orchestrator.py (Phase 3 Pattern B).

Locks in:
- Span tree: ls.pattern.run[pattern=orchestrator] → plan + worker.<n> + synthesize.
- Hardcoded depth=1 enforcement (P1 guard).
- Mini-tier blocked by default; override requires explicit flag.
- F7 telephone-game: synthesize prompt contains ≤ 200 char summaries only.
- F2: mini override forces workers to share lead model.
- Path B end-to-end on stub runner without DeepAgents machinery.
"""
from __future__ import annotations

import asyncio

import pytest


class _RecordingRunner:
    """Runner stub that records prompts and returns scripted contents."""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.calls = 0
        self.turn_history: list[dict] = []

    async def run_turn(self, prompt, *, model_ref=None, tools=None, system=None, ctx=None):
        self.turn_history.append({
            "prompt": prompt,
            "model_ref": model_ref,
            "system": system,
            "ctx": ctx,
        })
        if self.calls >= len(self.scripts):
            raise AssertionError(
                f"_RecordingRunner out of scripts (calls={self.calls}, "
                f"last_prompt={prompt[:120]!r})"
            )
        content = self.scripts[self.calls]
        self.calls += 1
        return {"content": content}


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


# Standard 2-worker decomposition JSON used by several tests.
_STD_DECOMPOSITION = (
    '[{"role": "researcher", "prompt": "Find the capital of France"}, '
    '{"role": "analyzer", "prompt": "Explain why Paris is the capital"}]'
)


def test_orchestrator_span_shape():
    """Root + plan + 2 workers + synthesize. All expected attrs set."""
    from localsmartz.patterns import orchestrator

    exporter = _install_in_memory_tracing()

    runner = _RecordingRunner([
        _STD_DECOMPOSITION,          # plan
        "Paris is the capital of France.",  # worker 1
        "Paris is the capital because...",  # worker 2
        "## Final\n\nParis is the capital of France.",  # synthesize
    ])

    profile = {
        "tier": "standard",
        "provider": "ollama",
        "use_deepagents_subagents": False,
        "budget": {},
    }
    agents = {
        "orchestrator": {"model_ref": {"provider": "ollama", "name": "qwen3.5:27b"}},
        "worker": {"model_ref": {"provider": "ollama", "name": "qwen3.5:9b"}},
    }

    events = _drain(orchestrator.run(
        "What is the capital of France and why?",
        agents=agents,
        profile=profile,
        runner=runner,
        ctx={"thread_id": "th-orch-1"},
    ))

    spans = exporter.get_finished_spans()
    names = [s.name for s in spans]

    # Root span with standard attrs.
    roots = [s for s in spans if s.name == "ls.pattern.run"]
    assert roots, f"missing ls.pattern.run; got {names}"
    root = roots[-1]
    attrs = dict(root.attributes)
    assert attrs.get("pattern") == "orchestrator"
    assert attrs.get("ls.thread.id") == "th-orch-1"
    assert attrs.get("ls.orchestrator.verdict") == "ok"
    assert attrs.get("ls.orchestrator.worker_count") == 2

    # Plan + synthesize + 2 worker children.
    assert any(s.name == "ls.orchestrator.plan" for s in spans)
    assert any(s.name == "ls.orchestrator.synthesize" for s in spans)
    worker_spans = [s for s in spans if s.name.startswith("ls.orchestrator.worker.")]
    assert len(worker_spans) == 2, f"expected 2 worker spans; got {len(worker_spans)}"
    for ws in worker_spans:
        for req in ("ls.worker.role", "ls.worker.artifact_id", "ls.worker.duration_ms", "ls.worker.status"):
            assert req in ws.attributes, (
                f"{ws.name} missing {req}; got {dict(ws.attributes)}"
            )

    # Event ordering contract.
    types = [e.get("type") for e in events]
    assert types[0] == "pattern_start"
    assert types[-1] == "done"


def test_orchestrator_depth_guard_raises():
    """P1 guard: depth >= 1 on entry raises NestedSubagentError."""
    from localsmartz.patterns import orchestrator

    _install_in_memory_tracing()
    runner = _RecordingRunner(["ignored"])

    profile = {
        "tier": "standard",
        "provider": "ollama",
        "use_deepagents_subagents": False,
    }
    agents = {
        "orchestrator": {"model_ref": {"provider": "ollama", "name": "x"}},
        "worker": {"model_ref": {"provider": "ollama", "name": "x"}},
    }

    with pytest.raises(orchestrator.NestedSubagentError):
        _drain(orchestrator.run(
            "nested call",
            agents=agents,
            profile=profile,
            runner=runner,
            ctx={"_orchestrator_depth": 1},
        ))


def test_orchestrator_mini_tier_blocks_without_override():
    """Mini tier without override emits error event + done, no worker turns."""
    from localsmartz.patterns import orchestrator

    _install_in_memory_tracing()
    runner = _RecordingRunner([])  # No calls expected.

    profile = {
        "tier": "mini",
        "provider": "ollama",
        "use_deepagents_subagents": False,
    }
    agents = {
        "orchestrator": {"model_ref": {"provider": "ollama", "name": "qwen3.5:9b"}},
        "worker": {"model_ref": {"provider": "ollama", "name": "qwen3.5:9b"}},
    }

    events = _drain(orchestrator.run(
        "ping", agents=agents, profile=profile, runner=runner, ctx={}
    ))

    types = [e.get("type") for e in events]
    assert "error" in types, f"expected error event on mini tier; got {types}"
    error = next(e for e in events if e.get("type") == "error")
    assert "standard or full tier" in error.get("content", "")
    assert runner.calls == 0, "mini-tier block must not consume runner calls"


def test_orchestrator_mini_tier_override_forces_shared_model(caplog):
    """F2 on mini override: workers share lead's model_ref."""
    from localsmartz.patterns import orchestrator

    _install_in_memory_tracing()
    runner = _RecordingRunner([
        _STD_DECOMPOSITION,
        "w1 output", "w2 output",
        "final",
    ])

    profile = {
        "tier": "mini",
        "provider": "ollama",
        "use_deepagents_subagents": False,
        "override_mini_orchestrator": True,
    }
    lead_model = {"provider": "ollama", "name": "qwen3.5:9b"}
    worker_model = {"provider": "ollama", "name": "qwen3.5:27b"}
    agents = {
        "orchestrator": {"model_ref": lead_model},
        "worker": {"model_ref": worker_model},
    }

    import logging
    caplog.set_level(logging.INFO, logger="localsmartz.patterns.orchestrator")

    _drain(orchestrator.run(
        "ping", agents=agents, profile=profile, runner=runner, ctx={}
    ))

    assert any(
        "forcing all workers to share" in r.message for r in caplog.records
    ), "F2 colocation log missing"

    # Every worker turn must have used the lead's model_ref.
    worker_turns = runner.turn_history[1:3]  # skip plan (0), last is synth (3)
    for t in worker_turns:
        assert t["model_ref"] == lead_model, (
            f"mini override must force worker model=lead; got {t['model_ref']}"
        )


def test_orchestrator_reads_summaries_not_raw_output():
    """F7 guard: synthesize prompt contains ≤ 200 char worker summary, not raw body."""
    from localsmartz.patterns import orchestrator

    _install_in_memory_tracing()

    big_worker_output = "X" * 2000  # 2000 chars
    runner = _RecordingRunner([
        _STD_DECOMPOSITION,
        big_worker_output,
        big_worker_output,
        "final",
    ])

    profile = {
        "tier": "standard",
        "provider": "ollama",
        "use_deepagents_subagents": False,
    }
    agents = {
        "orchestrator": {"model_ref": {"provider": "ollama", "name": "x"}},
        "worker": {"model_ref": {"provider": "ollama", "name": "x"}},
    }

    _drain(orchestrator.run(
        "ping", agents=agents, profile=profile, runner=runner, ctx={}
    ))

    # The final call is the synthesize turn. Its prompt must NOT contain
    # the raw 2000-char body; each worker summary must be ≤ 200 chars.
    synth_turn = runner.turn_history[-1]
    synth_prompt = synth_turn["prompt"]
    assert big_worker_output not in synth_prompt, (
        "synthesize prompt MUST NOT include raw worker body (F7 guard)"
    )
    # Each worker summary is <=200 chars (not counting the scaffolding).
    # The scaffolding format injects ``  [i] role (artifact_id=...): <summary>``.
    # Every worker's summary is a single "X…" block at most 200 chars.
    for line in synth_prompt.splitlines():
        if line.strip().startswith("[") and "):" in line:
            summary = line.split("):", 1)[1].strip()
            assert len(summary) <= 200, (
                f"worker summary exceeds 200 chars: {len(summary)}"
            )


def test_orchestrator_path_b_without_deepagents():
    """use_deepagents_subagents=False runs end-to-end on stub runner."""
    from localsmartz.patterns import orchestrator

    _install_in_memory_tracing()

    runner = _RecordingRunner([
        _STD_DECOMPOSITION,
        "paris is the capital",
        "because of history",
        "FINAL: Paris is the capital",
    ])

    profile = {
        "tier": "standard",
        "provider": "ollama",
        "use_deepagents_subagents": False,
    }
    agents = {
        "orchestrator": {"model_ref": {"provider": "ollama", "name": "x"}},
        "worker": {"model_ref": {"provider": "ollama", "name": "x"}},
    }

    events = _drain(orchestrator.run(
        "capital of france?",
        agents=agents,
        profile=profile,
        runner=runner,
        ctx={},
    ))

    # 4 runner turns expected: plan + 2 workers + synthesize.
    assert runner.calls == 4
    final = next(e for e in events if e.get("role") == "final")
    assert "FINAL" in final.get("content", "")


def test_orchestrator_path_a_falls_back_on_not_implemented():
    """Path A placeholder raises NotImplementedError → pattern falls back to Path B."""
    from localsmartz.patterns import orchestrator

    _install_in_memory_tracing()

    runner = _RecordingRunner([
        _STD_DECOMPOSITION,
        "w1", "w2",
        "final",
    ])
    profile = {
        "tier": "standard",
        "provider": "ollama",
        "use_deepagents_subagents": True,  # Path A requested
    }
    agents = {
        "orchestrator": {"model_ref": {"provider": "ollama", "name": "x"}},
        "worker": {"model_ref": {"provider": "ollama", "name": "x"}},
    }

    events = _drain(orchestrator.run(
        "ping", agents=agents, profile=profile, runner=runner, ctx={}
    ))

    types = [e.get("type") for e in events]
    # Verify we still completed (Path A fallback → Path B).
    assert types[-1] == "done"
    assert runner.calls == 4


def test_orchestrator_empty_decomposition_yields_final():
    """Lead returns non-JSON / empty list → pattern returns the plan body."""
    from localsmartz.patterns import orchestrator

    _install_in_memory_tracing()

    runner = _RecordingRunner([
        "I don't need to decompose — the answer is simply 42.",
    ])
    profile = {
        "tier": "standard",
        "provider": "ollama",
        "use_deepagents_subagents": False,
    }
    agents = {
        "orchestrator": {"model_ref": {"provider": "ollama", "name": "x"}},
        "worker": {"model_ref": {"provider": "ollama", "name": "x"}},
    }

    events = _drain(orchestrator.run(
        "answer me", agents=agents, profile=profile, runner=runner, ctx={}
    ))

    types = [e.get("type") for e in events]
    assert types[-1] == "done"
    assert runner.calls == 1
    final = next(e for e in events if e.get("role") == "final")
    assert "42" in final.get("content", "")
