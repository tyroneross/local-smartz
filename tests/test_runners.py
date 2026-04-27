"""Runner protocol + LocalOllamaRunner tests.

Cloud runners are tested in ``test_cloud_runners.py``.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from localsmartz.runners import AgentRunner, LocalOllamaRunner, get_runner
from localsmartz.runners.local_ollama import _should_disable_reasoning


def test_local_runner_conforms_to_protocol() -> None:
    runner: AgentRunner = LocalOllamaRunner()
    assert hasattr(runner, "run_turn")
    assert asyncio.iscoroutinefunction(runner.run_turn)


def test_get_runner_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError):
        get_runner("pigeonmail")


def test_reasoning_off_flag_respects_registry() -> None:
    # qwen3.5 family is flagged off-by-default in catalog.py.
    assert _should_disable_reasoning("qwen3.5:9b") is True
    assert _should_disable_reasoning("qwen3.5:9b-q4_K_M") is True
    # Unknown models -> no injection.
    assert _should_disable_reasoning("totally-made-up:1b") is False
    # gemma4 is reasoning_mode=native, not off-by-default.
    assert _should_disable_reasoning("gemma4:26b") is False


def test_local_runner_run_turn_normalizes_response(monkeypatch) -> None:
    """Patch ChatOllama to a stub and assert the AssistantTurn shape."""
    fake_msg = MagicMock()
    fake_msg.content = "hello"
    fake_msg.tool_calls = [{"name": "web_search", "args": {"query": "x"}, "id": "t1"}]
    fake_msg.usage_metadata = {
        "input_tokens": 10,
        "output_tokens": 5,
        "total_tokens": 15,
    }

    class FakeChatOllama:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs
            self.tools: list = []

        def bind_tools(self, tools):
            self.tools = tools
            return self

        async def ainvoke(self, messages):
            return fake_msg

    monkeypatch.setattr(
        "localsmartz.runners.local_ollama._build_chat_ollama",
        lambda ref: FakeChatOllama(),
    )

    runner = LocalOllamaRunner()
    result = asyncio.run(
        runner.run_turn(
            "hi",
            model_ref={"provider": "ollama", "name": "qwen3.5:9b"},
            system="be brief",
        )
    )
    assert result["content"] == "hello"
    assert result["tool_calls"][0]["name"] == "web_search"
    assert result["usage"]["total_tokens"] == 15
    assert result["model"] == "qwen3.5:9b"
    assert result["provider"] == "ollama"
