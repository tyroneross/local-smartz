"""Token-level streaming tests (feat: c6) for cloud runners.

Mocks the SDK stream interfaces; never hits network.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from localsmartz.runners.cloud_anthropic import CloudAnthropicRunner
from localsmartz.runners.cloud_openai_compat import CloudOpenAICompatRunner


def _run_aiter(aiter_call):
    async def _collect():
        out = []
        async for chunk in aiter_call:
            out.append(chunk)
        return out

    return asyncio.run(_collect())


# --- Anthropic stream_turn ----------------------------------------------


def test_anthropic_stream_yields_deltas_then_final():
    runner = CloudAnthropicRunner(api_key="sk-test")

    async def fake_text_stream():
        for piece in ["Hel", "lo ", "world"]:
            yield piece

    final_msg = SimpleNamespace(
        usage=SimpleNamespace(input_tokens=10, output_tokens=3),
    )

    @asynccontextmanager
    async def fake_stream(**kwargs: Any):
        s = SimpleNamespace()
        s.text_stream = fake_text_stream()
        s.get_final_message = AsyncMock(return_value=final_msg)
        yield s

    mock_messages = MagicMock()
    mock_messages.stream = fake_stream  # context manager factory
    runner._client = MagicMock(messages=mock_messages)

    chunks = _run_aiter(
        runner.stream_turn(
            "hi",
            model_ref={"provider": "anthropic", "name": "claude-haiku-4"},
            system="sys",
        )
    )

    deltas = [c["delta"] for c in chunks if not c.get("done")]
    assert "".join(deltas) == "Hello world"

    # Last chunk is terminal with final AssistantTurn.
    last = chunks[-1]
    assert last["done"] is True
    assert "final" in last
    final = last["final"]
    assert final["content"] == "Hello world"
    assert final["usage"]["input_tokens"] == 10
    assert final["usage"]["output_tokens"] == 3


def test_anthropic_stream_error_yields_terminal():
    runner = CloudAnthropicRunner(api_key="sk-test")

    @asynccontextmanager
    async def boom(**kwargs: Any):
        raise RuntimeError("stream broke")
        yield  # unreachable

    mock_messages = MagicMock()
    mock_messages.stream = boom
    runner._client = MagicMock(messages=mock_messages)

    chunks = _run_aiter(
        runner.stream_turn(
            "hi", model_ref={"provider": "anthropic", "name": "claude-haiku-4"}, system=None,
        )
    )
    assert len(chunks) == 1
    assert chunks[0]["done"] is True
    assert "stream broke" in chunks[0]["error"]


# --- OpenAI/Groq stream_turn --------------------------------------------


def _make_chunk(content: str | None = None, usage: dict | None = None):
    """Build a minimal OpenAI streaming chunk shape."""
    delta = SimpleNamespace(content=content)
    choice = SimpleNamespace(delta=delta) if content is not None else SimpleNamespace(delta=SimpleNamespace(content=None))
    if usage:
        return SimpleNamespace(choices=[choice] if content is not None else [], usage=SimpleNamespace(**usage))
    return SimpleNamespace(choices=[choice], usage=None)


def test_openai_stream_yields_deltas_and_usage(monkeypatch):
    """End-to-end: chunks → text concatenation + final usage."""
    runner = CloudOpenAICompatRunner()

    async def fake_aiter():
        yield _make_chunk(content="Hel")
        yield _make_chunk(content="lo")
        yield _make_chunk(content=" world")
        yield _make_chunk(content=None, usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8})

    fake_stream_obj = fake_aiter()

    mock_completions = MagicMock()
    mock_completions.create = AsyncMock(return_value=fake_stream_obj)
    mock_chat = MagicMock(completions=mock_completions)
    fake_client = MagicMock(chat=mock_chat)

    monkeypatch.setattr(runner, "_make_client", lambda *a, **kw: fake_client)

    chunks = _run_aiter(
        runner.stream_turn(
            "hi",
            model_ref={"provider": "openai", "name": "gpt-4o-mini"},
        )
    )

    deltas = [c["delta"] for c in chunks if not c.get("done")]
    assert "".join(deltas) == "Hello world"

    last = chunks[-1]
    assert last["done"] is True
    final = last["final"]
    assert final["content"] == "Hello world"
    assert final["usage"]["input_tokens"] == 5
    assert final["usage"]["output_tokens"] == 3


def test_openai_stream_error_yields_terminal(monkeypatch):
    runner = CloudOpenAICompatRunner()

    async def boom(**kwargs: Any) -> Any:
        raise RuntimeError("stream broke")

    mock_completions = MagicMock()
    mock_completions.create = AsyncMock(side_effect=boom)
    fake_client = MagicMock(chat=MagicMock(completions=mock_completions))

    monkeypatch.setattr(runner, "_make_client", lambda *a, **kw: fake_client)

    chunks = _run_aiter(
        runner.stream_turn(
            "hi", model_ref={"provider": "groq", "name": "llama-3.3-70b-versatile"},
        )
    )
    assert len(chunks) == 1
    assert chunks[0]["done"] is True
    assert "stream broke" in chunks[0]["error"]


# --- Final-event-required invariant -------------------------------------


def test_anthropic_stream_always_terminates_with_done():
    """Even on error path, last chunk MUST have done=True."""
    runner = CloudAnthropicRunner(api_key="sk-test")

    @asynccontextmanager
    async def fail_stream(**kwargs: Any):
        raise ValueError("nope")
        yield

    runner._client = MagicMock(messages=MagicMock(stream=fail_stream))
    chunks = _run_aiter(
        runner.stream_turn("x", model_ref={"provider": "anthropic", "name": "y"})
    )
    assert chunks[-1]["done"] is True
