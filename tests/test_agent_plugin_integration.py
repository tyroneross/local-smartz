"""Tests for plugin integration in agent.py.

Hermetic: sets HOME to a tmpdir so the Registry reads from an empty root.
Does not invoke ChatOllama — covers the prompt / tool assembly via the
extracted ``_build_system_prompt`` and ``_build_tool_set`` helpers.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from localsmartz import global_config
from localsmartz.agent import _build_system_prompt, _build_tool_set
from localsmartz.plugins import Registry
from localsmartz.plugins.agent_integration import (
    build_mcp_tools,
    build_plugin_tools,
    build_skill_prompt_section,
    get_active_skills,
)
from localsmartz.profiles import get_profile


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect HOME so Registry default root + global config live in tmp."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # windows safety
    # global_config DEFAULT paths read Path.home() lazily via _defaults().
    return tmp_path


def _write_plugin(
    root: Path,
    name: str,
    with_skill_name: str | None = None,
    with_command_name: str | None = None,
    with_mcp_spec: dict | None = None,
) -> Path:
    plugin_dir = root / name
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "0.1.0",
                "description": "Test plugin for agent integration.",
                "author": {"name": "Tester"},
            }
        )
    )
    if with_skill_name:
        skill_dir = plugin_dir / "skills" / with_skill_name
        skill_dir.mkdir(parents=True)
        long_desc = (
            "Integration-test skill description that is long enough to satisfy "
            "validators requiring a meaningful summary of what the skill does."
        )
        body = "BODY_MARKER_" + with_skill_name + "\n" + ("detail line\n" * 20)
        (skill_dir / "SKILL.md").write_text(
            "---\n"
            f"name: {with_skill_name}\n"
            f"description: {long_desc}\n"
            "version: 1.0.0\n"
            "---\n"
            + body
        )
    if with_command_name:
        cmd_dir = plugin_dir / "commands"
        cmd_dir.mkdir(parents=True)
        (cmd_dir / f"{with_command_name}.md").write_text(
            "---\n"
            f"description: Command {with_command_name} for integration test.\n"
            "allowed-tools: Bash, Read\n"
            "---\n"
            f"Command body for {with_command_name}.\n"
        )
    if with_mcp_spec is not None:
        mcp_path = plugin_dir / ".mcp.json"
        mcp_path.write_text(json.dumps({"mcpServers": with_mcp_spec}))
    return plugin_dir


def _install(fake_home: Path, plugin_src: Path) -> Registry:
    reg = Registry(fake_home / ".localsmartz" / "plugins")
    reg.install(plugin_src)
    return reg


# ---------------------------------------------------------------------------
# Skills-as-prompt (Test 1 & 2)
# ---------------------------------------------------------------------------


def test_system_prompt_without_active_skills(fake_home, tmp_path):
    """No active skills -> no 'Available Skills' section."""
    # Install a skill but don't activate it.
    src = tmp_path / "src"
    src.mkdir()
    _write_plugin(src, "p1", with_skill_name="unused-skill")
    _install(fake_home, src / "p1")

    profile = get_profile("full")
    prompt = _build_system_prompt(profile, include_plugin_skills=True)
    assert "Available Skills" not in prompt


def test_system_prompt_with_active_skill(fake_home, tmp_path):
    """Active skill -> name and body appear in prompt."""
    src = tmp_path / "src"
    src.mkdir()
    _write_plugin(src, "p1", with_skill_name="alpha-skill")
    _install(fake_home, src / "p1")

    # Activate via global_config (the runtime source of truth).
    global_config.set("active_skills", ["alpha-skill"])

    profile = get_profile("full")
    prompt = _build_system_prompt(profile, include_plugin_skills=True)

    assert "Available Skills" in prompt
    assert "alpha-skill" in prompt
    assert "BODY_MARKER_alpha-skill" in prompt


def test_system_prompt_include_flag_false(fake_home, tmp_path):
    """include_plugin_skills=False suppresses the section even when active."""
    src = tmp_path / "src"
    src.mkdir()
    _write_plugin(src, "p1", with_skill_name="alpha-skill")
    _install(fake_home, src / "p1")
    global_config.set("active_skills", ["alpha-skill"])

    profile = get_profile("full")
    prompt = _build_system_prompt(profile, include_plugin_skills=False)
    assert "Available Skills" not in prompt


def test_build_skill_prompt_section_empty():
    assert build_skill_prompt_section([]) == ""


# ---------------------------------------------------------------------------
# Commands-as-tools (Test 3)
# ---------------------------------------------------------------------------


def test_plugin_tools_full_profile_has_command(fake_home, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_plugin(src, "cmd-plugin", with_command_name="runner")
    _install(fake_home, src / "cmd-plugin")

    tools = build_plugin_tools(profile_name="full")
    assert len(tools) >= 1
    names = {getattr(t, "name", "") for t in tools}
    assert any("plugin_cmd_plugin_runner" in n for n in names)


def test_plugin_tools_lite_returns_empty(fake_home, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_plugin(src, "cmd-plugin", with_command_name="runner")
    _install(fake_home, src / "cmd-plugin")

    assert build_plugin_tools(profile_name="lite") == []


# ---------------------------------------------------------------------------
# MCP-as-tools (Test 4)
# ---------------------------------------------------------------------------


def test_mcp_tools_empty_registry(fake_home):
    tools, clients = build_mcp_tools()
    assert tools == []
    assert clients == []


def test_mcp_tools_handles_failing_server(fake_home, tmp_path):
    """A server pointing to a nonexistent binary must be skipped gracefully."""
    src = tmp_path / "src"
    src.mkdir()
    _write_plugin(
        src,
        "broken-mcp",
        with_mcp_spec={
            "ghost": {
                "command": "/definitely/not/a/real/binary-xyzzy",
                "args": [],
            }
        },
    )
    _install(fake_home, src / "broken-mcp")

    tools, clients = build_mcp_tools()
    # Broken server is skipped; no crash, no tools, no lingering clients.
    assert tools == []
    assert clients == []


# ---------------------------------------------------------------------------
# _build_tool_set composition
# ---------------------------------------------------------------------------


def test_build_tool_set_full_includes_plugin_tool(fake_home, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_plugin(src, "cmd-plugin", with_command_name="runner")
    _install(fake_home, src / "cmd-plugin")

    profile = get_profile("full")
    tools, mcp_clients = _build_tool_set(
        profile, include_plugin_tools=True, include_mcp=False
    )
    names = {getattr(t, "name", "") for t in tools}
    assert any("plugin_cmd_plugin_runner" in n for n in names)
    assert mcp_clients == []


def test_build_tool_set_lite_skips_plugin_tools(fake_home, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _write_plugin(src, "cmd-plugin", with_command_name="runner")
    _install(fake_home, src / "cmd-plugin")

    profile = get_profile("lite")
    tools, mcp_clients = _build_tool_set(
        profile, include_plugin_tools=True, include_mcp=False
    )
    names = {getattr(t, "name", "") for t in tools}
    # Lite path forces plugin tools off inside build_plugin_tools().
    assert not any("plugin_cmd_plugin_runner" in n for n in names)
    assert mcp_clients == []


def test_get_active_skills_returns_empty_when_unconfigured(fake_home):
    assert get_active_skills() == []
