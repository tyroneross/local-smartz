"""Regression: /api/research must use resolve_available_model() when the
configured planning model isn't pulled, instead of hard-failing with
"Model not found → ollama pull ...". Mirror of the CLI _preflight() and
/api/status behavior."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from localsmartz import serve


class _FakeHandler(serve.LocalSmartzHandler):
    """Bare minimum surface to drive _stream_research without a real socket.

    Subclasses the real handler so phase helpers like ``_preflight_model``
    resolve through inheritance, but skips the socket-bound constructor.
    """

    _model_override = None
    _default_profile = "lite"

    def __init__(self):  # noqa: D401 — intentional override
        self.events: list[dict] = []

    def _send_event(self, data: dict):
        self.events.append(data)

    def _start_sse(self):
        pass


def test_research_falls_back_when_planning_model_missing(monkeypatch, tmp_path):
    """When the configured planning model isn't available but a substitute is,
    the SSE stream emits a [note] text event and proceeds — does NOT emit an
    'error' event saying 'ollama pull ...'."""

    handler = _FakeHandler()

    monkeypatch.setattr("localsmartz.ollama.check_server", lambda: True)
    monkeypatch.setattr("localsmartz.ollama.model_available", lambda m: False)
    monkeypatch.setattr(
        "localsmartz.ollama.list_models_with_size",
        lambda: [("qwen3:8b-q4_K_M", 5.2), ("gpt-oss:120b", 65.0)],
    )
    monkeypatch.setattr("localsmartz.ollama.list_models", lambda: ["qwen3:8b-q4_K_M", "gpt-oss:120b"])
    monkeypatch.setattr("localsmartz.serve._saved_model_override", lambda _cwd: None)

    # Stop short of actually creating an agent — we only care about the
    # preflight branch's behavior. Raise to bail out cleanly.
    def _explode(*args, **kwargs):
        raise RuntimeError("agent setup intentionally skipped in test")

    monkeypatch.setattr("localsmartz.agent.create_agent", _explode)

    # Profile with a model that won't be available.
    fake_profile = {
        "name": "lite",
        "planning_model": "llama3.1:70b-instruct-q5_K_M",
        "execution_model": "llama3.1:70b-instruct-q5_K_M",
        "max_turns": 5,
    }
    monkeypatch.setattr(
        "localsmartz.profiles.get_profile",
        lambda *_a, **_k: fake_profile,
    )

    try:
        serve.LocalSmartzHandler._stream_research(
            handler, prompt="hi", profile_name="lite", thread_id=None
        )
    except RuntimeError as e:
        # Expected — we deliberately bailed at create_agent.
        assert "intentionally skipped" in str(e)

    error_events = [e for e in handler.events if e.get("type") == "error"]
    text_events = [e for e in handler.events if e.get("type") == "text"]

    # Critical assertion: the bug emitted "Model 'llama3.1:70b-...' not found"
    # — that must NOT happen now.
    for e in error_events:
        assert "not found" not in e.get("message", ""), (
            f"regression: model fallback skipped, got error: {e}"
        )

    # We should have emitted a [note] text event mentioning the substitute.
    assert any("gpt-oss:120b" in e.get("content", "") for e in text_events), handler.events


def test_research_still_errors_when_no_fallback_exists(monkeypatch):
    """If the configured model is missing AND no substitute exists, the
    original 'ollama pull ...' error message must still surface so the user
    knows what to do."""
    handler = _FakeHandler()

    monkeypatch.setattr("localsmartz.ollama.check_server", lambda: True)
    monkeypatch.setattr("localsmartz.ollama.model_available", lambda m: False)
    monkeypatch.setattr("localsmartz.ollama.list_models_with_size", lambda: [])
    monkeypatch.setattr("localsmartz.ollama.list_models", lambda: [])
    monkeypatch.setattr("localsmartz.serve._saved_model_override", lambda _cwd: None)
    monkeypatch.setattr(
        "localsmartz.profiles.get_profile",
        lambda *_a, **_k: {
            "name": "lite",
            "planning_model": "llama3.1:70b-instruct-q5_K_M",
            "execution_model": "llama3.1:70b-instruct-q5_K_M",
            "max_turns": 5,
        },
    )

    serve.LocalSmartzHandler._stream_research(
        handler, prompt="hi", profile_name="lite", thread_id=None
    )

    error_events = [e for e in handler.events if e.get("type") == "error"]
    assert error_events, "expected an error event when no fallback is available"
    assert "ollama pull llama3.1:70b" in error_events[0]["message"]
