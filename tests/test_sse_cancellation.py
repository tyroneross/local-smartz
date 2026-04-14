"""Tests for SSE cancellation propagation.

When the Swift app cancels a research stream, the HTTP client closes the
TCP connection. The next ``_send_event`` write raises ``BrokenPipeError``.
These tests lock in the guarantee that:

1. The fast-path stream loop exits within one more chunk.
2. The full-agent stream loop exits within one more chunk.
3. ``pulse.stop()`` runs via the ``finally`` clause in both cases.
4. The post-stream ``agent.invoke`` + ``done`` event are skipped on
   disconnect (so we don't write to a closed socket).
"""

from __future__ import annotations

import threading
from typing import Any, Iterator
from unittest.mock import patch

from localsmartz.serve import LocalSmartzHandler


def _make_handler() -> LocalSmartzHandler:
    """Construct a handler without binding to a real socket.

    We bypass ``BaseHTTPRequestHandler.__init__`` (which expects request,
    client_address, server) and set only the attributes ``_stream_research``
    and ``_send_event`` touch.
    """
    h = LocalSmartzHandler.__new__(LocalSmartzHandler)
    h._sse_lock = threading.Lock()
    # Attribute used by ``_send_event`` — not exercised when we override it.
    return h


class _StubAgent:
    """Minimal stand-in for a LangGraph agent returned by ``create_agent``.

    ``stream`` yields ``(mode, payload)`` tuples mimicking
    ``stream_mode=["updates", "messages"]``. Each AIMessageChunk has a
    ``.content`` str so the messages branch emits it.
    """

    tools: list[Any] = []

    def __init__(self, n_chunks: int = 10):
        self._n = n_chunks

    def stream(self, _input, config=None, stream_mode=None) -> Iterator:
        for i in range(self._n):
            chunk = _AIChunk(f"token-{i}")
            yield ("messages", (chunk, {}))

    def invoke(self, *a, **kw):
        return {"messages": []}


class _AIChunk:
    def __init__(self, text: str):
        self.content = text
        self.type = "ai"


def test_fast_path_loop_breaks_on_broken_pipe():
    """Fast-path: simulate the 3rd _send_event raising BrokenPipeError.
    The for loop must exit within one more chunk and pulse.stop() must run.
    """
    handler = _make_handler()

    calls = {"n": 0}
    pulse_stopped = {"v": False}

    def fake_send(event):
        calls["n"] += 1
        if calls["n"] == 3:
            raise BrokenPipeError("client gone")

    handler._send_event = fake_send  # type: ignore[assignment]

    # Stub every dependency _stream_research imports so the fast-path branch
    # is reached quickly.
    class _StubPulse:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            pass

        def touch(self):
            pass

        def stop(self):
            pulse_stopped["v"] = True

    def fake_fast_path_stream(prompt, profile, model_override=None):
        for i in range(10):
            yield {"type": "text", "content": f"chunk-{i}"}
        yield {"type": "done", "duration_ms": 0}

    fake_profile = {
        "name": "lite",
        "planning_model": "m",
        "execution_model": "m",
        "max_turns": 20,
    }

    with patch("localsmartz.serve._HeartbeatPulse", _StubPulse), \
         patch("localsmartz.agent.fast_path_stream", fake_fast_path_stream), \
         patch("localsmartz.profiles.is_fast_path", return_value=True), \
         patch("localsmartz.profiles.get_profile", return_value=fake_profile), \
         patch("localsmartz.ollama.check_server", return_value=True), \
         patch("localsmartz.ollama.list_models", return_value=["m"]), \
         patch("localsmartz.ollama.warmup_model", return_value=(True, 0, None)), \
         patch("localsmartz.ollama.model_available", return_value=True):
        # Disable warmup + model-fallback side effects so we land in fast path.
        handler._stream_research("hi", "lite", None, None)

    # The loop iterated enough to trigger the break, then stopped.
    # We raise on call 3. Allow at most one more _send_event before exit.
    assert calls["n"] <= 4, f"loop kept writing after BrokenPipe: {calls['n']}"
    assert calls["n"] >= 3, "test didn't reach the broken-pipe simulation"
    assert pulse_stopped["v"], "pulse.stop() did not run"


def test_full_agent_loop_breaks_on_broken_pipe():
    """Full-agent: same contract — once _send_event raises, the loop exits
    and pulse.stop() runs, and the post-stream done event is NOT emitted.
    """
    handler = _make_handler()

    calls = {"n": 0, "done_emitted": False}
    pulse_stopped = {"v": False}

    def fake_send(event):
        calls["n"] += 1
        if event.get("type") == "done":
            calls["done_emitted"] = True
        if calls["n"] == 3:
            raise BrokenPipeError("client gone")

    handler._send_event = fake_send  # type: ignore[assignment]

    class _StubPulse:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            pass

        def touch(self):
            pass

        def stop(self):
            pulse_stopped["v"] = True

    fake_profile = {
        "name": "lite",
        "planning_model": "m",
        "execution_model": "m",
        "max_turns": 20,
    }

    stub_agent = _StubAgent(n_chunks=20)

    with patch("localsmartz.serve._HeartbeatPulse", _StubPulse), \
         patch("localsmartz.profiles.is_fast_path", return_value=False), \
         patch("localsmartz.profiles.get_profile", return_value=fake_profile), \
         patch("localsmartz.ollama.check_server", return_value=True), \
         patch("localsmartz.ollama.list_models", return_value=["m"]), \
         patch("localsmartz.ollama.warmup_model", return_value=(True, 0, None)), \
         patch("localsmartz.ollama.model_available", return_value=True), \
         patch("localsmartz.agent.create_agent", return_value=(stub_agent, fake_profile, None, None)):
        handler._stream_research("hi", "lite", None, None)

    assert calls["n"] <= 4, f"loop kept writing after BrokenPipe: {calls['n']}"
    assert calls["n"] >= 3, "test didn't reach the broken-pipe simulation"
    assert pulse_stopped["v"], "pulse.stop() did not run"
    assert not calls["done_emitted"], "done event was emitted after disconnect"
