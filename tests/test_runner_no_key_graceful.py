"""Cloud runners must raise a clear RuntimeError (NOT an SDK stack trace) when
no API key is available.

This is the C2 goal-criterion guard: provider-absent must degrade clearly,
not leak SDK internals.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from localsmartz.runners.cloud_anthropic import CloudAnthropicRunner
from localsmartz.runners.cloud_openai_compat import CloudOpenAICompatRunner


def _run(coro):
    return asyncio.run(coro)


def test_anthropic_runner_no_key_raises_runtime_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("localsmartz.secrets.get_secret", return_value=None, create=True):
        runner = CloudAnthropicRunner(api_key=None)  # explicit None overrides env probe
        runner._api_key = None  # belt-and-suspenders: force the unset state
        with pytest.raises(RuntimeError) as exc:
            _run(runner.run_turn("hi", model_ref={"provider": "anthropic", "name": "claude-haiku-4"}))
    msg = str(exc.value)
    assert "Anthropic" in msg or "anthropic" in msg
    assert "API key" in msg


@pytest.mark.parametrize("provider,env_var", [("openai", "OPENAI_API_KEY"), ("groq", "GROQ_API_KEY")])
def test_openai_compat_runner_no_key_raises_runtime_error(monkeypatch, provider, env_var):
    monkeypatch.delenv(env_var, raising=False)
    with patch("localsmartz.secrets.get_secret", return_value=None, create=True):
        runner = CloudOpenAICompatRunner()
        with pytest.raises(RuntimeError) as exc:
            _run(runner.run_turn("hi", model_ref={"provider": provider, "name": "x"}))
    msg = str(exc.value)
    assert provider in msg
    assert "API key" in msg
