"""Anthropic prompt-caching tests (feat: c2).

Mocks the SDK so no real API calls happen. Asserts:
- Request payload carries `cache_control` on system + last tool entry
- Response usage with cache fields propagates to AssistantTurn["usage"]
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from localsmartz.runners.cloud_anthropic import CloudAnthropicRunner


def _run(coro):
    return asyncio.run(coro)


def _fake_resp(*, with_cache_fields: bool):
    """Build a minimal SDK-shaped response object."""
    text_block = SimpleNamespace(type="text", text="hello world")
    usage_kwargs = {"input_tokens": 100, "output_tokens": 20}
    if with_cache_fields:
        usage_kwargs["cache_creation_input_tokens"] = 80
        usage_kwargs["cache_read_input_tokens"] = 0
    usage = SimpleNamespace(**usage_kwargs)
    return SimpleNamespace(content=[text_block], usage=usage)


def _make_runner_with_mock(resp: Any) -> tuple[CloudAnthropicRunner, MagicMock]:
    """Build a runner with a stubbed AsyncAnthropic client, returning the messages.create mock."""
    runner = CloudAnthropicRunner(api_key="sk-test-fake")
    mock_messages = MagicMock()
    mock_messages.create = AsyncMock(return_value=resp)
    mock_client = MagicMock(messages=mock_messages)
    runner._client = mock_client
    return runner, mock_messages.create


def test_cache_control_on_system_block():
    runner, create_mock = _make_runner_with_mock(_fake_resp(with_cache_fields=False))

    _run(
        runner.run_turn(
            "user prompt",
            model_ref={"provider": "anthropic", "name": "claude-haiku-4"},
            system="You are a helpful assistant.",
        )
    )

    kwargs = create_mock.call_args.kwargs
    sys_field = kwargs["system"]
    assert isinstance(sys_field, list), "system should be a list of typed blocks for caching"
    assert len(sys_field) == 1
    assert sys_field[0]["type"] == "text"
    assert sys_field[0]["text"] == "You are a helpful assistant."
    assert sys_field[0]["cache_control"] == {"type": "ephemeral"}


def test_no_system_means_no_system_field():
    """When system is None we should not inject an empty list."""
    runner, create_mock = _make_runner_with_mock(_fake_resp(with_cache_fields=False))
    _run(
        runner.run_turn(
            "user prompt",
            model_ref={"provider": "anthropic", "name": "claude-haiku-4"},
            system=None,
        )
    )
    kwargs = create_mock.call_args.kwargs
    assert "system" not in kwargs


def test_cache_control_on_last_tool():
    runner, create_mock = _make_runner_with_mock(_fake_resp(with_cache_fields=False))

    fake_tool_a = SimpleNamespace(name="search", description="search the web", args_schema=None)
    fake_tool_b = SimpleNamespace(name="scrape", description="scrape a URL", args_schema=None)

    _run(
        runner.run_turn(
            "user prompt",
            tools=[fake_tool_a, fake_tool_b],
            model_ref={"provider": "anthropic", "name": "claude-haiku-4"},
        )
    )

    kwargs = create_mock.call_args.kwargs
    tools_payload = kwargs.get("tools")
    assert isinstance(tools_payload, list) and len(tools_payload) == 2
    assert "cache_control" not in tools_payload[0], "first tool should not have cache_control"
    assert tools_payload[1]["cache_control"] == {"type": "ephemeral"}, (
        "last tool should carry cache_control to cache the whole tools array"
    )


def test_no_tools_means_no_tools_field():
    runner, create_mock = _make_runner_with_mock(_fake_resp(with_cache_fields=False))
    _run(
        runner.run_turn(
            "user prompt",
            tools=None,
            model_ref={"provider": "anthropic", "name": "claude-haiku-4"},
        )
    )
    assert "tools" not in create_mock.call_args.kwargs


def test_cache_usage_fields_propagate_to_assistant_turn():
    runner, _ = _make_runner_with_mock(_fake_resp(with_cache_fields=True))

    turn = _run(
        runner.run_turn(
            "user prompt",
            model_ref={"provider": "anthropic", "name": "claude-haiku-4"},
            system="sys",
        )
    )

    usage = turn["usage"]
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 20
    assert usage["cache_creation_input_tokens"] == 80
    assert usage["cache_read_input_tokens"] == 0


def test_cache_fields_absent_when_sdk_omits_them():
    runner, _ = _make_runner_with_mock(_fake_resp(with_cache_fields=False))

    turn = _run(
        runner.run_turn(
            "user prompt",
            model_ref={"provider": "anthropic", "name": "claude-haiku-4"},
            system="sys",
        )
    )
    usage = turn["usage"]
    assert "cache_creation_input_tokens" not in usage
    assert "cache_read_input_tokens" not in usage
