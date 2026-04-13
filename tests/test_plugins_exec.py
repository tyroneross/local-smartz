"""Plugin command execution — fenced shell blocks run via subprocess with
timeout, stripped env, workspace cwd. Guidance-only fallback when Bash isn't
in allowed-tools or when no shell block is present."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from localsmartz.plugins.agent_integration import (
    _exec_shell,
    _extract_shell_block,
    _make_command_tool,
)
from localsmartz.plugins.loader import Command


def _cmd(body: str, allowed_tools: list[str], name: str = "demo", plugin: str = "p") -> Command:
    return Command(
        name=name,
        description="demo command",
        allowed_tools=allowed_tools,
        body=body,
        source_path=Path("/tmp/fake.md"),
        plugin_name=plugin,
    )


def _invoke(tool, arguments: str = "") -> str:
    """Call the tool whether it's a StructuredTool or a plain shim."""
    if hasattr(tool, "invoke"):
        try:
            return tool.invoke({"arguments": arguments})
        except Exception:
            pass
    if hasattr(tool, "func"):
        return tool.func(arguments)
    return tool(arguments)


# ---------------------------------------------------------------------------
# _extract_shell_block
# ---------------------------------------------------------------------------

def test_extract_bash_block():
    body = "Run this:\n\n```bash\necho hi\n```\n\nThen this."
    assert _extract_shell_block(body) == "echo hi"


def test_extract_sh_block():
    body = "```sh\nls -la\n```"
    assert _extract_shell_block(body) == "ls -la"


def test_extract_first_block_when_multiple():
    body = "```bash\nfirst\n```\n```bash\nsecond\n```"
    assert _extract_shell_block(body) == "first"


def test_extract_returns_none_when_no_block():
    assert _extract_shell_block("Just plain markdown.") is None


def test_extract_returns_none_on_empty():
    assert _extract_shell_block("") is None


# ---------------------------------------------------------------------------
# _exec_shell
# ---------------------------------------------------------------------------

def test_exec_returns_stdout():
    assert _exec_shell("echo hello", timeout=5).strip() == "hello"


def test_exec_timeout(monkeypatch):
    out = _exec_shell("sleep 5", timeout=1)
    assert "timed out" in out


def test_exec_strips_unsafe_env(monkeypatch):
    monkeypatch.setenv("SECRET_TOKEN", "supersecret")
    out = _exec_shell("echo \"${SECRET_TOKEN:-unset}\"", timeout=5)
    # The stripped env shouldn't include SECRET_TOKEN.
    assert "supersecret" not in out
    assert "unset" in out


def test_exec_nonzero_exit_includes_stderr_and_returncode():
    out = _exec_shell("echo oops >&2 && exit 7", timeout=5)
    assert "exit 7" in out
    assert "oops" in out


# ---------------------------------------------------------------------------
# _make_command_tool
# ---------------------------------------------------------------------------

def test_command_tool_executes_when_bash_allowed():
    cmd = _cmd("```bash\necho hi-from-plugin\n```", allowed_tools=["Bash"])
    tool = _make_command_tool(cmd)
    out = _invoke(tool)
    assert "hi-from-plugin" in out
    assert "[plugin:p/demo]" in out


def test_command_tool_guidance_when_bash_missing():
    cmd = _cmd("```bash\necho hi\n```", allowed_tools=["Read"])
    tool = _make_command_tool(cmd)
    out = _invoke(tool)
    assert "guidance-only" in out
    assert "echo hi" in out  # body included


def test_command_tool_guidance_when_no_shell_block():
    cmd = _cmd("Just markdown, no fenced block.", allowed_tools=["Bash"])
    tool = _make_command_tool(cmd)
    out = _invoke(tool)
    assert "guidance-only" in out
    assert "Just markdown" in out


def test_command_tool_includes_arguments_in_prefix():
    cmd = _cmd("```bash\ntrue\n```", allowed_tools=["Bash"])
    tool = _make_command_tool(cmd)
    out = _invoke(tool, arguments="some args")
    assert "args='some args'" in out


def test_command_tool_timeout(monkeypatch):
    cmd = _cmd("```bash\nsleep 5\n```", allowed_tools=["Bash"])
    tool = _make_command_tool(cmd, timeout=1)
    out = _invoke(tool)
    assert "timed out" in out
