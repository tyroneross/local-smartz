"""Activation-gap fix (work item 9): every runtime prompt-assembly call
site must route through profiles.get_role_prompt (which prefers the
editable agents/prompts/<role>.md over the AGENT_ROLES dict string), NOT
read the dict's system_focus directly. Before this fix, the UI's prompt
editor (PUT /api/agents/<role>/prompt) and list_agents DISPLAYED the .md
content while runtime EXECUTED the dict string — display and execution
disagreed.

These tests monkeypatch the prompts directory itself (rather than editing
real files under src/localsmartz/agents/, which is another implementer's
owned surface) so they're self-contained and don't collide with concurrent
prompt-wording edits.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def override_prompt(tmp_path, monkeypatch):
    """Point agents.definitions._PROMPTS_DIR at a temp dir with one
    distinctive override file for a given role. Returns a setter."""
    from localsmartz.agents import definitions as agent_defs

    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    monkeypatch.setattr(agent_defs, "_PROMPTS_DIR", prompts_dir)

    def _set(role: str, text: str) -> None:
        (prompts_dir / f"{role}.md").write_text(text, encoding="utf-8")

    return _set


MARKER = "MD-OVERRIDE-MARKER-9f3c"


def test_get_role_prompt_prefers_md_file(override_prompt):
    from localsmartz.profiles import get_role_prompt

    override_prompt("analyzer", f"{MARKER} analyzer body")
    assert get_role_prompt("analyzer") == f"{MARKER} analyzer body"


def test_build_subagent_specs_uses_md_override(override_prompt):
    """agent.py:_build_subagent_specs must read the .md override, not the
    AGENT_ROLES dict's system_focus string."""
    from localsmartz.agent import _build_subagent_specs
    from localsmartz.profiles import get_profile

    override_prompt("analyzer", f"{MARKER} analyzer body")
    profile = get_profile("full")
    specs = _build_subagent_specs(profile, all_tools=[])
    by_name = {s["name"]: s for s in specs}
    assert by_name["analyzer"]["system_prompt"] == f"{MARKER} analyzer body"


def test_focus_mode_uses_md_override(override_prompt, tmp_path):
    """agent.py's focus_agent branch must read the .md override for the
    main-agent system prompt."""
    override_prompt("analyzer", f"{MARKER} analyzer body")
    fake_deep_agent = MagicMock(name="fake_deep_agent")
    with patch("localsmartz.agent.ChatOllama") as mock_chat, \
         patch("localsmartz.agent.create_deep_agent", return_value=fake_deep_agent) as mock_create:
        mock_chat.return_value = MagicMock(name="chat_ollama_instance")
        from localsmartz.agent import create_agent
        create_agent(
            profile_name="full",
            cwd=tmp_path,
            focus_agent="analyzer",
            include_plugin_skills=False,
            include_plugin_tools=False,
        )
    sys_prompt = mock_create.call_args.kwargs.get("system_prompt", "")
    assert sys_prompt == f"{MARKER} analyzer body"


def test_orchestrator_main_prompt_uses_md_override(override_prompt, tmp_path):
    """agent.py's orchestrator main-agent prompt assembly must read the
    .md override, with the runtime roster suffix appended."""
    override_prompt("orchestrator", f"{MARKER} orchestrator body")
    fake_deep_agent = MagicMock(name="fake_deep_agent")
    with patch("localsmartz.agent.ChatOllama") as mock_chat, \
         patch("localsmartz.agent.create_deep_agent", return_value=fake_deep_agent) as mock_create:
        mock_chat.return_value = MagicMock(name="chat_ollama_instance")
        from localsmartz.agent import create_agent
        create_agent(
            profile_name="full",
            cwd=tmp_path,
            include_plugin_skills=False,
            include_plugin_tools=False,
        )
    main_prompt = mock_create.call_args.kwargs.get("system_prompt", "")
    assert main_prompt.startswith(f"{MARKER} orchestrator body")
    assert "Available roles this run:" in main_prompt


def test_pipeline_role_system_prompt_uses_md_override(override_prompt):
    """pipeline.py:_role_system_prompt must read the .md override."""
    override_prompt("writer", f"{MARKER} writer body")
    from localsmartz.pipeline import _role_system_prompt

    assert _role_system_prompt("writer") == f"{MARKER} writer body"


def test_pipeline_role_system_prompt_falls_back_to_dict_without_md():
    """No .md file for a role → falls back to the AGENT_ROLES dict string
    (unchanged legacy behavior), never a hard crash."""
    from localsmartz.pipeline import _role_system_prompt
    from localsmartz.profiles import AGENT_ROLES

    # 'writer' always has a real .md file on disk in this repo checkout,
    # so exercise the true no-file case via an unknown role name instead.
    assert _role_system_prompt("totally-unknown-role") == ""
    # Sanity: a real role without monkeypatching still returns non-empty
    # text (either from its real .md file or the dict fallback).
    assert _role_system_prompt("writer").strip()
    assert AGENT_ROLES["writer"]["system_focus"]  # dict fallback still exists
