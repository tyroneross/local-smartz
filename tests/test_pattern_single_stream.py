"""patterns.single stream_turn integration (commit D, 2026-05-08).

Closes the c6 deferral. The cloud runners
(``cloud_anthropic.stream_turn`` / ``cloud_openai_compat.stream_turn``)
yield ``StreamChunk{delta, done}`` chunks but had no caller. After this
commit, ``patterns.single.run`` consumes them when available, emitting
``text_delta`` PatternEvents for each chunk before the final ``turn``
event.

Fallback: runners without ``stream_turn`` (local_ollama, harmony) keep the
prior whole-turn behavior. ``stream=False`` also forces the fallback.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

import pytest

from localsmartz.patterns import single


def _drain(coro_iter: Any) -> list[dict]:
    async def _go() -> list[dict]:
        out: list[dict] = []
        async for ev in coro_iter:
            out.append(ev)
        return out

    return asyncio.run(_go())


class _StreamingRunner:
    """Fake runner with both run_turn and stream_turn.

    stream_turn yields the supplied deltas, then a terminal done chunk
    carrying the assembled AssistantTurn. Tests verify the pattern emits
    one ``text_delta`` per delta plus one ``turn``.
    """

    def __init__(self, deltas: list[str]) -> None:
        self.deltas = deltas
        self.run_turn_calls = 0
        self.stream_calls = 0

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
        return {
            "content": "".join(self.deltas),
            "tool_calls": [],
            "usage": {"input_tokens": 10, "output_tokens": 10},
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
        self.stream_calls += 1
        for d in self.deltas:
            yield {"delta": d, "done": False}
        yield {
            "delta": "",
            "done": True,
            "final": {
                "content": "".join(self.deltas),
                "tool_calls": [],
                "usage": {"input_tokens": 10, "output_tokens": 10},
                "model": model_ref.get("name", ""),
                "provider": model_ref.get("provider", ""),
            },
        }


class _NonStreamingRunner:
    """Fake runner WITHOUT stream_turn — exercises the fallback path."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.run_turn_calls = 0

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
        return {
            "content": self.content,
            "tool_calls": [],
            "usage": {"input_tokens": 5, "output_tokens": 5},
        }


def test_stream_turn_emits_text_delta_per_chunk(monkeypatch):
    """3-delta stream → 3 text_delta events + 1 turn + 1 done (+ pattern_start)."""
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)

    runner = _StreamingRunner(deltas=["Hello", " ", "world"])
    agents = {
        "primary": {
            "model_ref": {"provider": "anthropic", "name": "claude-haiku-4"},
        }
    }
    profile = {"name": "full"}

    events = _drain(
        single.run(
            "test",
            agents=agents,
            profile=profile,
            stream=True,
            runner=runner,
        )
    )

    types = [ev.get("type") for ev in events]
    assert types.count("pattern_start") == 1
    assert types.count("text_delta") == 3
    assert types.count("turn") == 1
    assert types.count("done") == 1
    # stream_turn was used, not run_turn:
    assert runner.stream_calls == 1
    assert runner.run_turn_calls == 0
    # Order: pattern_start, 3x text_delta, turn, done
    deltas = [ev.get("delta") for ev in events if ev.get("type") == "text_delta"]
    assert deltas == ["Hello", " ", "world"]
    # Final turn carries the full assembled content
    turn_ev = next(ev for ev in events if ev.get("type") == "turn")
    assert turn_ev["content"] == "Hello world"
    assert turn_ev["role"] == "primary"


def test_fallback_when_runner_lacks_stream_turn(monkeypatch):
    """Runner without stream_turn → falls back to run_turn (no text_delta)."""
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)

    runner = _NonStreamingRunner(content="fallback works")
    agents = {
        "primary": {
            "model_ref": {"provider": "ollama", "name": "qwen3:8b"},
        }
    }
    profile = {"name": "lite"}

    events = _drain(
        single.run("test", agents=agents, profile=profile, stream=True, runner=runner)
    )

    types = [ev.get("type") for ev in events]
    assert "text_delta" not in types  # fallback — no streaming events
    assert types.count("turn") == 1
    assert types.count("done") == 1
    assert runner.run_turn_calls == 1
    turn_ev = next(ev for ev in events if ev.get("type") == "turn")
    assert turn_ev["content"] == "fallback works"


def test_stream_false_forces_run_turn_fallback(monkeypatch):
    """``stream=False`` must use run_turn even when stream_turn is available."""
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)

    runner = _StreamingRunner(deltas=["a", "b"])
    agents = {"primary": {"model_ref": {"provider": "groq", "name": "x"}}}
    profile = {"name": "full"}

    events = _drain(
        single.run("test", agents=agents, profile=profile, stream=False, runner=runner)
    )
    types = [ev.get("type") for ev in events]
    assert "text_delta" not in types
    assert runner.run_turn_calls == 1
    assert runner.stream_calls == 0


class _ErroringStreamRunner:
    """stream_turn yields one delta then errors out — exercises the error path."""

    async def run_turn(self, *a, **kw):  # pragma: no cover (not exercised)
        raise NotImplementedError

    async def stream_turn(self, prompt, **kw) -> AsyncIterator[dict]:
        yield {"delta": "partial", "done": False}
        yield {"delta": "", "done": True, "error": "rate limited"}


def test_stream_error_terminates_cleanly(monkeypatch):
    """A streamed error must surface as a turn event with [error] prefix.

    The pattern doesn't crash on a stream-side error; it produces a
    deterministic terminal event so callers can render an error message.
    """
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)
    runner = _ErroringStreamRunner()
    agents = {"primary": {"model_ref": {"provider": "anthropic", "name": "x"}}}
    profile = {"name": "full"}

    events = _drain(
        single.run("test", agents=agents, profile=profile, stream=True, runner=runner)
    )
    types = [ev.get("type") for ev in events]
    assert "text_delta" in types  # the partial delta surfaced
    turn_ev = next(ev for ev in events if ev.get("type") == "turn")
    assert "[error]" in turn_ev["content"]
    assert "rate limited" in turn_ev["content"]
