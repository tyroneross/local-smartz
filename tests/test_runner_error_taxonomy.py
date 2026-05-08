"""Tier-3 hygiene tests (feat: c10): error taxonomy + OTel span audit + dual-path doc.

Verifies:
- Runner* error classes exist and form a coherent hierarchy
- Anthropic SDK exceptions map to the right Runner* class
- OpenAI/Groq SDK exceptions map similarly
- Dual-path doc blocks reference each other + the deepagents memory
- No re-introduction of RunnableRetry on the chat-model wrapper layer
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from localsmartz.runners.base import (
    RunnerAuth,
    RunnerContextOverflow,
    RunnerError,
    RunnerRateLimit,
    RunnerTransient,
    RunnerUnknown,
)
from localsmartz.runners.cloud_anthropic import (
    CloudAnthropicRunner,
    _map_anthropic_error,
)
from localsmartz.runners.cloud_openai_compat import (
    CloudOpenAICompatRunner,
    _map_openai_error,
)


def _run(coro):
    return asyncio.run(coro)


# --- error class hierarchy ----------------------------------------------


def test_runner_error_hierarchy():
    """All specific classes subclass RunnerError, which subclasses Exception."""
    for cls in (RunnerAuth, RunnerRateLimit, RunnerTransient, RunnerContextOverflow, RunnerUnknown):
        assert issubclass(cls, RunnerError)
        assert issubclass(cls, Exception)


def test_broad_except_still_catches():
    """Existing 'except Exception:' code paths must still catch normalized errors."""
    try:
        raise RunnerAuth("test")
    except Exception as e:
        assert isinstance(e, RunnerError)


# --- _map_anthropic_error -----------------------------------------------


def test_map_anthropic_401_is_auth():
    """401 status → RunnerAuth via the status_code branch."""

    class FakeAuth(Exception):
        status_code = 401

    err = _map_anthropic_error(FakeAuth("denied"))
    assert isinstance(err, RunnerAuth)


def test_map_anthropic_429_via_status():
    class FakeRL(Exception):
        status_code = 429

    err = _map_anthropic_error(FakeRL("slow"))
    assert isinstance(err, RunnerRateLimit)


def test_map_anthropic_class_name_rate_limit():
    class RateLimitError(Exception):
        pass

    err = _map_anthropic_error(RateLimitError("slow"))
    assert isinstance(err, RunnerRateLimit)


def test_map_anthropic_auth_class_name():
    class AuthenticationError(Exception):
        pass

    err = _map_anthropic_error(AuthenticationError("bad key"))
    assert isinstance(err, RunnerAuth)


def test_map_anthropic_context_overflow():
    class BadRequest(Exception):
        status_code = 400

    err = _map_anthropic_error(BadRequest("the prompt is too long for the model"))
    assert isinstance(err, RunnerContextOverflow)


def test_map_anthropic_httpx_transient():
    err = _map_anthropic_error(httpx.TimeoutException("read timeout"))
    assert isinstance(err, RunnerTransient)


def test_map_anthropic_unknown():
    err = _map_anthropic_error(ValueError("strange"))
    assert isinstance(err, RunnerUnknown)


# --- _map_openai_error --------------------------------------------------


def test_map_openai_403_auth():
    class PermissionDeniedError(Exception):
        status_code = 403

    err = _map_openai_error(PermissionDeniedError("nope"))
    assert isinstance(err, RunnerAuth)


def test_map_openai_429_rate_limit():
    class RL(Exception):
        status_code = 429

    err = _map_openai_error(RL("slow"))
    assert isinstance(err, RunnerRateLimit)


def test_map_openai_context_overflow_via_code():
    class BR(Exception):
        status_code = 400
        code = "context_length_exceeded"

    err = _map_openai_error(BR("too long"))
    assert isinstance(err, RunnerContextOverflow)


def test_map_openai_transient():
    err = _map_openai_error(httpx.ConnectError("refused"))
    assert isinstance(err, RunnerTransient)


# --- Runner integration -------------------------------------------------


def test_anthropic_runner_remaps_auth_after_call():
    """The runner's adapter layer turns SDK auth errors into RunnerAuth."""
    runner = CloudAnthropicRunner(api_key="sk-fake")

    class AuthErr(Exception):
        status_code = 401

    mock_messages = MagicMock()
    mock_messages.create = AsyncMock(side_effect=AuthErr("forbidden"))
    runner._client = MagicMock(messages=mock_messages)

    with pytest.raises(RunnerAuth):
        _run(
            runner.run_turn(
                "hi",
                model_ref={"provider": "anthropic", "name": "claude-haiku-4"},
            )
        )


def test_openai_runner_remaps_unknown(monkeypatch):
    runner = CloudOpenAICompatRunner()

    class StrangeError(Exception):
        pass

    mock_completions = MagicMock()
    mock_completions.create = AsyncMock(side_effect=StrangeError("???"))
    fake_client = MagicMock(chat=MagicMock(completions=mock_completions))
    monkeypatch.setattr(runner, "_make_client", lambda *a, **kw: fake_client)

    with pytest.raises(RunnerUnknown):
        _run(
            runner.run_turn(
                "hi",
                model_ref={"provider": "openai", "name": "gpt-4o-mini"},
            )
        )


# --- Dual-path doc invariant -------------------------------------------


def test_dual_path_doc_in_agent_py():
    """agent.py::_create_model docstring references factory.py + memory note."""
    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "src" / "localsmartz" / "agent.py").read_text()
    # The c10 doc block must mention the deepagents memory file path.
    assert "reference_deepagents_runnable_retry.md" in src
    # Must reference the factory function explicitly.
    assert "create_langchain_model" in src
    assert "DUAL-PATH" in src or "DO NOT collapse" in src or "DO NOT" in src


def test_dual_path_doc_in_factory_py():
    """factory.py docstring references agent.py + memory note."""
    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "src" / "localsmartz" / "runners" / "factory.py").read_text()
    assert "reference_deepagents_runnable_retry.md" in src
    assert "_create_model" in src
    assert "agent.py" in src


def test_no_runnable_retry_in_factory():
    """factory.create_langchain_model must not wrap models in retry — that
    breaks DeepAgents per the dual-path doc.

    Docstrings/comments referencing RunnableRetry are fine; we forbid only
    actual usage in code.
    """
    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "src" / "localsmartz" / "runners" / "factory.py").read_text()
    # Strip docstrings + comments before checking.
    code_only_lines = []
    in_docstring = False
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith('"""') or stripped.startswith("'''"):
            # Toggle docstring state on triple-quote OPEN/CLOSE; bare-line case.
            count = stripped.count('"""') + stripped.count("'''")
            if count == 1:
                in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        # Strip line comments.
        if "#" in line:
            line = line.split("#", 1)[0]
        code_only_lines.append(line)
    code = "\n".join(code_only_lines)
    assert ".with_retry(" not in code
    assert "RunnableRetry(" not in code
    assert "from langchain_core.runnables import RunnableRetry" not in code


def test_create_model_does_not_wrap_with_retry():
    """agent.py::_create_model returns a bare model. The fast-path-stream
    helper later in the same file uses .with_retry() because it never
    enters DeepAgents — that's fine. We just check _create_model itself."""
    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "src" / "localsmartz" / "agent.py").read_text()
    # Find the _create_model function body.
    idx = src.index("def _create_model(")
    next_def = src.index("\ndef _active_provider(", idx)
    body = src[idx:next_def]
    assert ".with_retry(" not in body, (
        "_create_model body uses .with_retry — DeepAgents will break"
    )
