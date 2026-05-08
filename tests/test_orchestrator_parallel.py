"""Tests for parallel worker dispatch in orchestrator pattern (feat: c5).

Verifies:
- _dispatch_workers_path_b uses asyncio.gather (workers run concurrently)
- Result-list ordering preserved (worker 1 → index 0, etc.)
- Cloud providers respect a per-provider concurrency cap
- Ollama runs are unbounded
- NestedSubagentError still raises and propagates
- Per-worker errors are captured into status="error" without raising the batch
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import MagicMock

import pytest

from localsmartz.patterns.orchestrator import (
    NestedSubagentError,
    WorkerTask,
    _dispatch_workers_path_b,
)


def _run(coro):
    return asyncio.run(coro)


class _StubTracer:
    """Minimal tracer; matches the get_tracer() span context manager API."""

    class _Span:
        def __enter__(self):
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def set_attribute(self, *args: Any, **kwargs: Any) -> None:
            pass

    def start_as_current_span(self, name: str) -> "_StubTracer._Span":
        return self._Span()


class _SlowRunner:
    """Stub runner that sleeps for `delay` then returns a fake AssistantTurn.

    Records start/end timestamps per call to confirm overlap.
    """

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.starts: list[float] = []
        self.ends: list[float] = []

    async def run_turn(self, prompt: str, *, tools: Any = None, model_ref: dict, system: str | None = None, ctx: dict | None = None) -> dict:
        self.starts.append(time.time())
        await asyncio.sleep(self.delay)
        self.ends.append(time.time())
        return {
            "content": f"reply for {prompt}",
            "tool_calls": [],
            "usage": {},
            "model": model_ref.get("name", ""),
            "provider": model_ref.get("provider", ""),
            "raw": None,
        }


def _make_tasks(n: int) -> list[WorkerTask]:
    return [WorkerTask(role=f"role_{i}", prompt=f"task_{i}") for i in range(1, n + 1)]


def _stub_register_artifact(monkeypatch):
    """Patch artifact registration so we don't actually write to disk."""
    monkeypatch.setattr(
        "localsmartz.patterns.orchestrator.register_artifact",
        lambda **kw: {"id": f"art-{kw.get('path', 'x').split('/')[-1]}"},
    )


def test_dispatch_runs_in_parallel_for_ollama(monkeypatch):
    """4 ollama workers each sleeping 0.2s should finish in ~0.2s, not ~0.8s."""
    _stub_register_artifact(monkeypatch)
    runner = _SlowRunner(delay=0.2)
    tasks = _make_tasks(4)
    worker_agent = {"model_ref": {"provider": "ollama", "name": "qwen3:8b"}, "tools": [], "system_focus": "sys"}

    started = time.time()
    results = _run(
        _dispatch_workers_path_b(
            tasks,
            runner=runner,  # type: ignore[arg-type]
            lead_ref={"provider": "ollama", "name": "lead"},
            profile={},
            worker_agent=worker_agent,
            ctx={},
            forced_worker_ref={},
            tracer=_StubTracer(),
            thread_id="t1",
        )
    )
    elapsed = time.time() - started

    # Parallel: should be roughly delay (with small overhead), not 4× delay.
    assert elapsed < 0.6, f"workers ran serially? elapsed={elapsed:.2f}s"
    assert len(results) == 4
    # Order preserved: result i corresponds to tasks[i].
    for i, r in enumerate(results):
        assert r["role"] == f"role_{i+1}"
        assert r["status"] == "ok"


def test_dispatch_respects_cloud_concurrency_cap(monkeypatch):
    """6 cloud (anthropic) workers with cap=4 should NOT all run simultaneously."""
    _stub_register_artifact(monkeypatch)
    runner = _SlowRunner(delay=0.2)
    tasks = _make_tasks(6)
    worker_agent = {"model_ref": {"provider": "anthropic", "name": "claude-haiku-4"}, "tools": []}

    results = _run(
        _dispatch_workers_path_b(
            tasks,
            runner=runner,  # type: ignore[arg-type]
            lead_ref={"provider": "anthropic", "name": "lead"},
            profile={},
            worker_agent=worker_agent,
            ctx={},
            forced_worker_ref={},
            tracer=_StubTracer(),
            thread_id="t-cap",
        )
    )

    assert len(results) == 6
    # Determine concurrent peak from start/end timestamps. With cap=4 and
    # 6 tasks of equal length, the peak in-flight count must be ≤ 4.
    events: list[tuple[float, int]] = []
    for s in runner.starts:
        events.append((s, +1))
    for e in runner.ends:
        events.append((e, -1))
    events.sort()
    peak = 0
    cur = 0
    for _, d in events:
        cur += d
        peak = max(peak, cur)
    assert peak <= 4, f"peak in-flight {peak} exceeds cap 4"


def test_dispatch_preserves_order(monkeypatch):
    """Even when workers finish at different times, result list mirrors task order."""
    _stub_register_artifact(monkeypatch)

    class _VariableRunner:
        async def run_turn(self, prompt: str, *, tools: Any = None, model_ref: dict, system: str | None = None, ctx: dict | None = None) -> dict:
            # Reverse order: task_3 finishes first, task_1 last.
            n = int(prompt.split("_")[1])
            await asyncio.sleep(0.1 / n)
            return {"content": f"out_{n}", "tool_calls": [], "usage": {}, "model": "", "provider": "ollama", "raw": None}

    runner = _VariableRunner()
    tasks = _make_tasks(3)
    worker_agent = {"model_ref": {"provider": "ollama", "name": "x"}, "tools": []}

    results = _run(
        _dispatch_workers_path_b(
            tasks,
            runner=runner,  # type: ignore[arg-type]
            lead_ref={"provider": "ollama", "name": "lead"},
            profile={},
            worker_agent=worker_agent,
            ctx={},
            forced_worker_ref={},
            tracer=_StubTracer(),
            thread_id="t-ord",
        )
    )

    assert [r["role"] for r in results] == ["role_1", "role_2", "role_3"]


def test_nested_subagent_error_propagates(monkeypatch):
    """When ctx already has _orchestrator_depth>=1, raising is mandatory."""
    _stub_register_artifact(monkeypatch)
    runner = _SlowRunner(delay=0.0)

    with pytest.raises(NestedSubagentError):
        _run(
            _dispatch_workers_path_b(
                _make_tasks(2),
                runner=runner,  # type: ignore[arg-type]
                lead_ref={"provider": "ollama", "name": "lead"},
                profile={},
                worker_agent={"model_ref": {"provider": "ollama", "name": "x"}, "tools": []},
                ctx={"_orchestrator_depth": 1},
                forced_worker_ref={},
                tracer=_StubTracer(),
                thread_id="t-nest",
            )
        )


def test_per_worker_error_captured(monkeypatch):
    """A worker raising mid-call → status='error' on that worker; batch continues."""
    _stub_register_artifact(monkeypatch)

    class _OneFails:
        async def run_turn(self, prompt: str, *, tools: Any = None, model_ref: dict, system: str | None = None, ctx: dict | None = None) -> dict:
            if prompt == "task_2":
                raise RuntimeError("stub failure")
            return {"content": "ok", "tool_calls": [], "usage": {}, "model": "", "provider": "ollama", "raw": None}

    runner = _OneFails()
    results = _run(
        _dispatch_workers_path_b(
            _make_tasks(3),
            runner=runner,  # type: ignore[arg-type]
            lead_ref={"provider": "ollama", "name": "lead"},
            profile={},
            worker_agent={"model_ref": {"provider": "ollama", "name": "x"}, "tools": []},
            ctx={},
            forced_worker_ref={},
            tracer=_StubTracer(),
            thread_id="t-fail",
        )
    )
    assert len(results) == 3
    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "error"
    assert "stub failure" in results[1]["summary"]
    assert results[2]["status"] == "ok"
