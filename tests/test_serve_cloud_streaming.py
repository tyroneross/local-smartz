"""Cloud-runner SSE bridge tests (commit B, 2026-05-08).

Closes the round-2 ❓ Unfixed: the cloud-runner streaming primitive
(``cloud_anthropic.stream_turn``, ``cloud_openai_compat.stream_turn``)
ships token deltas, but ``_stream_research`` previously routed cloud
requests through the Ollama-bound fast/graph/full paths, so the SSE
consumer never saw ``text_delta`` events for cloud calls.

After this commit:
- ``provider in (anthropic, openai, groq)`` skips ``_preflight_model``
  entirely (cloud calls don't require ``ollama serve``).
- The patterns-layer ``run()`` is dispatched and its PatternEvents are
  translated to SSE events:
    text_delta → {type: "text", content: delta}   (token-level streaming)
    turn       → dropped when streamed already; emitted as text otherwise
    budget_warn / iteration / phase_*  → pass-through
    done       → swallowed; orchestrator emits its own wrapper done with
                 duration_ms.
"""
from __future__ import annotations

from typing import Any, AsyncIterator
from unittest.mock import patch

import pytest

from localsmartz import serve


class _FakeHandler(serve.LocalSmartzHandler):
    """Bare-minimum handler subclass so phase helpers resolve through
    inheritance, but socket-bound construction is skipped."""

    _model_override = None
    _default_profile = "lite"

    def __init__(self):  # noqa: D401 — intentional override
        self.events: list[dict] = []

    def _send_event(self, data: dict):
        self.events.append(data)

    def _start_sse(self):
        pass


class _StreamingFakeRunner:
    """stream_turn yields the supplied deltas, then a terminal done chunk
    carrying the assembled AssistantTurn. Mirrors the test double in
    ``tests/test_pattern_single_stream._StreamingRunner`` so the contract
    stays identical across the two layers we care about.
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
        self.stream_calls += 1
        for d in self.deltas:
            yield {"delta": d, "done": False}
        yield {
            "delta": "",
            "done": True,
            "final": {
                "content": "".join(self.deltas),
                "tool_calls": [],
                "usage": {"input_tokens": 5, "output_tokens": 5},
                "model": model_ref.get("name", ""),
                "provider": model_ref.get("provider", ""),
            },
        }


def test_cloud_provider_streams_text_delta_via_sse(monkeypatch, tmp_path):
    """provider=groq should bypass Ollama preflight AND emit type=text events
    in delta order before the final done event."""
    handler = _FakeHandler()

    runner = _StreamingFakeRunner(deltas=["The ", "answer ", "is 42"])
    monkeypatch.setattr("localsmartz.runners.get_runner", lambda p: runner)

    # Disable the budget threshold reader so the test doesn't depend on
    # filesystem state. Same pattern other pattern tests use.
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)

    # Hard guard: if any code path tries to run Ollama preflight we want a
    # loud failure rather than a hang.
    def _explode(*a, **kw):
        raise RuntimeError("Ollama preflight should NOT run for cloud providers")

    monkeypatch.setattr("localsmartz.ollama.check_server", _explode)
    monkeypatch.setattr("localsmartz.ollama.ensure_model_ready", _explode)

    handler._stream_research(
        prompt="What is the answer?",
        profile_name="lite",
        thread_id=None,
        focus_agent=None,
        cwd_override=str(tmp_path),
        pattern="single",
        provider="groq",
    )

    # 1. Streaming was used (stream_turn called, run_turn was not).
    assert runner.stream_calls == 1
    assert runner.run_turn_calls == 0

    # 2. The SSE stream contains exactly the three text deltas, in order.
    text_events = [e for e in handler.events if e.get("type") == "text"]
    assert [e["content"] for e in text_events] == ["The ", "answer ", "is 42"]

    # 3. Exactly one done event closes the stream (orchestrator wrapper —
    # the pattern's own done event is swallowed).
    dones = [e for e in handler.events if e.get("type") == "done"]
    assert len(dones) == 1
    assert isinstance(dones[0].get("duration_ms"), int)


def test_unknown_cloud_provider_emits_error(monkeypatch, tmp_path):
    """A cloud provider not in CLOUD_TIER_TABLE must surface an error event,
    not crash the handler."""
    handler = _FakeHandler()
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)

    handler._stream_research(
        prompt="hi",
        profile_name="lite",
        thread_id=None,
        focus_agent=None,
        cwd_override=str(tmp_path),
        pattern="single",
        provider="bogus-provider",
    )

    errors = [e for e in handler.events if e.get("type") == "error"]
    assert errors, f"expected an error event, got: {handler.events}"
    assert "bogus-provider" in errors[0]["message"]


def test_unknown_pattern_emits_error(monkeypatch, tmp_path):
    """Unknown pattern name must surface a friendly error, not AttributeError."""
    handler = _FakeHandler()
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)

    runner = _StreamingFakeRunner(deltas=["x"])
    monkeypatch.setattr("localsmartz.runners.get_runner", lambda p: runner)

    handler._stream_research(
        prompt="hi",
        profile_name="lite",
        thread_id=None,
        focus_agent=None,
        cwd_override=str(tmp_path),
        pattern="not-a-pattern",
        provider="groq",
    )

    errors = [e for e in handler.events if e.get("type") == "error"]
    assert errors
    assert "not-a-pattern" in errors[0]["message"].lower() or \
           "unknown pattern" in errors[0]["message"].lower()


def test_provider_ollama_does_not_take_cloud_path(monkeypatch, tmp_path):
    """provider=ollama (or None) must use the existing routing, not the
    cloud bridge. We assert by monkeypatching get_runner to explode — if
    the cloud branch is taken, get_runner is called and the test fails."""
    handler = _FakeHandler()
    monkeypatch.setattr("localsmartz.serve._read_budget_threshold", lambda: None)

    def _explode(p):
        raise AssertionError(
            f"_run_pattern_cloud was called for provider={p!r} — should not happen"
        )

    monkeypatch.setattr("localsmartz.runners.get_runner", _explode)

    # Stub the ollama preflight so the test doesn't try to hit a real daemon.
    # We don't care about its outcome; we just need the cloud branch to be
    # skipped.
    def _preflight_stub(profile_name, model_override):
        # Returning None terminates _stream_research immediately (matches
        # the "error already emitted" sentinel).
        handler._send_event({"type": "_preflight_called"})
        return None

    monkeypatch.setattr(handler, "_preflight_model", _preflight_stub)

    handler._stream_research(
        prompt="hi",
        profile_name="lite",
        thread_id=None,
        focus_agent=None,
        cwd_override=str(tmp_path),
        pattern="single",
        provider="ollama",
    )

    # The preflight stub was reached — i.e. the cloud bridge was bypassed.
    assert any(e.get("type") == "_preflight_called" for e in handler.events)
