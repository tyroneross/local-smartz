"""Tests for per-agent model routing.

Covers:
- ``list_agents`` emits the new {name, title, summary, model} shape
- global_config["agent_models"] overrides merge correctly
- ``create_agent(focus_agent="analyzer")`` picks up the analyzer's configured
  model (without requiring a live Ollama server — we patch ChatOllama to avoid
  network I/O and interrogate the call args)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from localsmartz import global_config
from localsmartz.profiles import (
    PROFILES,
    effective_agent_models,
    get_agent_model,
    get_profile,
    list_agents,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect HOME so global_config writes land in tmp."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


# ── list_agents returns the new shape ────────────────────────────────────

def test_list_agents_full_returns_dict_with_model(fake_home):
    profile = get_profile("full")
    agents = list_agents(profile)
    # list_agents filters out main-agent-only roles (orchestrator) so the UI
    # sidebar only surfaces pickable specialists. Orchestrator runs as the
    # main agent by default; exposing it as focus would scope the main
    # agent's tools to [] and lock out delegation.
    assert len(agents) == 5
    names = {a["name"] for a in agents}
    assert names == {
        "planner",
        "researcher",
        "analyzer",
        "writer",
        "fact_checker",
    }
    assert "orchestrator" not in names
    for a in agents:
        assert "name" in a
        assert "title" in a
        assert "summary" in a
        assert "model" in a
        assert isinstance(a["model"], str) and a["model"]


def test_list_agents_lite_returns_dict_with_model(fake_home):
    profile = get_profile("lite")
    agents = list_agents(profile)
    assert len(agents) == 4
    for a in agents:
        assert a["model"] == "qwen3:8b-q4_K_M"


def test_list_agents_full_analyzer_uses_32b(fake_home):
    profile = get_profile("full")
    by_name = {a["name"]: a for a in list_agents(profile)}
    assert "32b" in by_name["analyzer"]["model"]
    assert "8b" in by_name["planner"]["model"]


# ── global_config override takes precedence ──────────────────────────────

def test_list_agents_honors_override(fake_home):
    global_config.set("agent_models", {"analyzer": "gemma2:9b"})
    profile = get_profile("full")
    by_name = {a["name"]: a for a in list_agents(profile)}
    assert by_name["analyzer"]["model"] == "gemma2:9b"
    # Other agents untouched
    assert by_name["planner"]["model"] == PROFILES["full"]["agents"]["planner"]["model"]


def test_get_agent_model_override_wins(fake_home):
    global_config.set("agent_models", {"writer": "llama3.2:3b"})
    profile = get_profile("full")
    assert get_agent_model(profile, "writer") == "llama3.2:3b"


def test_get_agent_model_returns_default_without_override(fake_home):
    profile = get_profile("full")
    default = PROFILES["full"]["agents"]["analyzer"]["model"]
    assert get_agent_model(profile, "analyzer") == default


def test_get_agent_model_unknown_returns_none(fake_home):
    profile = get_profile("full")
    assert get_agent_model(profile, "ghost") is None


def test_effective_agent_models_merges(fake_home):
    global_config.set("agent_models", {"planner": "custom:x"})
    profile = get_profile("full")
    eff = effective_agent_models(profile)
    assert eff["planner"] == "custom:x"
    assert eff["analyzer"] == PROFILES["full"]["agents"]["analyzer"]["model"]


# ── create_agent picks up focus_agent's model ────────────────────────────

def test_create_agent_focus_agent_picks_configured_model(fake_home, tmp_path, monkeypatch):
    """When focus_agent is set, the ChatOllama model should match the agent's
    configured model — no live Ollama call required.
    """
    # Stub create_deep_agent so it doesn't exercise a live model.
    fake_deep_agent = MagicMock(name="fake_deep_agent")

    with patch("localsmartz.agent.ChatOllama") as mock_chat, \
         patch("localsmartz.agent.create_deep_agent", return_value=fake_deep_agent) as mock_create:
        # ChatOllama instance is the object create_deep_agent receives as `model`.
        mock_chat.return_value = MagicMock(name="chat_ollama_instance")

        from localsmartz.agent import create_agent

        agent, profile, ckpt, mcp_clients = create_agent(
            profile_name="full",
            cwd=tmp_path,
            focus_agent="analyzer",
            include_plugin_skills=False,
            include_plugin_tools=False,
        )

    # The analyzer's configured model should have been used to build ChatOllama.
    kwargs = mock_chat.call_args.kwargs
    analyzer_model = PROFILES["full"]["agents"]["analyzer"]["model"]
    assert kwargs.get("model") == analyzer_model

    # Profile planning_model should have been bumped to the analyzer's model
    # so downstream code (logging, status) agrees.
    assert profile["planning_model"] == analyzer_model

    # Focus mode now *replaces* the system prompt with the role's system_focus
    # (previously it was an appended "## Single-Agent Mode" section). Since
    # DeepAgents' subagents list is passed empty in focus mode, the main
    # agent *is* the role and speaks in that voice directly.
    call_kwargs = mock_create.call_args.kwargs
    sys_prompt = call_kwargs.get("system_prompt", "")
    assert "ANALYZER agent" in sys_prompt
    # subagents=[] in focus mode — the role runs as the main agent, no
    # ``task`` delegation to avoid small-model tool-call hallucinations.
    assert call_kwargs.get("subagents") == []
    # Main agent's tools should be scoped to the analyzer's allow-list.
    tools_passed = call_kwargs.get("tools") or []
    tool_names = {getattr(t, "name", None) or getattr(t, "__name__", None) for t in tools_passed}
    # Analyzer is allowed python_exec + read_file + write_file + ls.
    assert "python_exec" in tool_names
    # Analyzer is NOT allowed web_search — scoping prevents the researcher's
    # tools from bleeding into the focused agent.
    assert "web_search" not in tool_names


def test_multi_agent_mode_passes_scoped_subagents(fake_home, tmp_path):
    """Default (no focus) mode should pass subagents=[...] where each
    entry has a narrow tool allow-list. Proves the DeepAgents migration:
    the planner subagent must not see researcher/analyzer tools."""
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

    call_kwargs = mock_create.call_args.kwargs
    subagents = call_kwargs.get("subagents") or []
    by_name = {s["name"]: s for s in subagents}

    # All specialist roles from AGENT_ROLES should be present as subagents
    # (the orchestrator runs as the MAIN agent, not as a subagent, so it's
    # absent from this list).
    assert {"planner", "researcher", "analyzer", "writer", "fact_checker"} <= set(by_name.keys())
    assert "orchestrator" not in by_name  # main-agent role, not a subagent

    # Per-agent tool scoping — the list here is the CUSTOM tool overlay that
    # sits on top of DeepAgents' always-on middleware tools (write_todos,
    # ls, read_file, write_file, edit_file, glob, grep). Planner gets no
    # custom tools — the tighter surface is what blocks
    # ``repo_browser.*`` tool-name hallucinations.
    planner_tool_names = {
        getattr(t, "name", None) or getattr(t, "__name__", None)
        for t in by_name["planner"]["tools"]
    }
    assert "web_search" not in planner_tool_names
    assert "python_exec" not in planner_tool_names

    # Analyzer gets python_exec but NOT web_search.
    analyzer_tool_names = {
        getattr(t, "name", None) or getattr(t, "__name__", None)
        for t in by_name["analyzer"]["tools"]
    }
    assert "python_exec" in analyzer_tool_names
    assert "web_search" not in analyzer_tool_names

    # Researcher gets web_search + scrape_url + parse_pdf.
    researcher_tool_names = {
        getattr(t, "name", None) or getattr(t, "__name__", None)
        for t in by_name["researcher"]["tools"]
    }
    assert "web_search" in researcher_tool_names
    assert "scrape_url" in researcher_tool_names
    # Researcher should not be computing things.
    assert "python_exec" not in researcher_tool_names


def test_create_agent_focus_agent_respects_override(fake_home, tmp_path):
    """A per-user override via global_config must beat the profile default."""
    global_config.set("agent_models", {"analyzer": "override:7b"})

    fake_deep_agent = MagicMock(name="fake_deep_agent")
    with patch("localsmartz.agent.ChatOllama") as mock_chat, \
         patch("localsmartz.agent.create_deep_agent", return_value=fake_deep_agent):
        mock_chat.return_value = MagicMock(name="chat_ollama_instance")

        from localsmartz.agent import create_agent

        agent, profile, ckpt, mcp_clients = create_agent(
            profile_name="full",
            cwd=tmp_path,
            focus_agent="analyzer",
            include_plugin_skills=False,
            include_plugin_tools=False,
        )

    assert mock_chat.call_args.kwargs.get("model") == "override:7b"
    assert profile["planning_model"] == "override:7b"


def test_create_agent_explicit_model_override_wins(fake_home, tmp_path):
    """CLI --model (passed through as model_override) still wins over per-agent."""
    fake_deep_agent = MagicMock(name="fake_deep_agent")
    with patch("localsmartz.agent.ChatOllama") as mock_chat, \
         patch("localsmartz.agent.create_deep_agent", return_value=fake_deep_agent):
        mock_chat.return_value = MagicMock(name="chat_ollama_instance")

        from localsmartz.agent import create_agent

        agent, profile, ckpt, mcp_clients = create_agent(
            profile_name="full",
            cwd=tmp_path,
            focus_agent="analyzer",
            model_override="explicit:42",
            include_plugin_skills=False,
            include_plugin_tools=False,
        )

    assert mock_chat.call_args.kwargs.get("model") == "explicit:42"


# ── Backward-compat shape assertions for /api/agents consumers ──────────

def test_list_agents_preserves_legacy_keys(fake_home):
    profile = get_profile("full")
    for a in list_agents(profile):
        # Frontend / Swift client still expects these keys
        assert isinstance(a.get("name"), str)
        assert isinstance(a.get("title"), str)
        assert isinstance(a.get("summary"), str)


# ── system_focus exposed to UI (Settings → Agents read-only viewer) ──────

def test_list_agents_exposes_system_focus(fake_home):
    """Every agent dict includes `system_focus` as a string. The Swift
    Settings → Agents tab renders this verbatim so users can inspect the
    role's actual system prompt without reading Python source."""
    from localsmartz.profiles import AGENT_ROLES

    profile = get_profile("full")
    for a in list_agents(profile):
        assert "system_focus" in a, f"{a['name']} missing system_focus"
        assert isinstance(a["system_focus"], str)
        # Matches the AGENT_ROLES source of truth for each exposed role.
        expected = AGENT_ROLES.get(a["name"], {}).get("system_focus", "")
        assert a["system_focus"] == expected


def test_list_agents_system_focus_nonempty_for_specialists(fake_home):
    """Specialist roles (planner/researcher/analyzer/writer/fact_checker)
    all have a nonempty system_focus — the UI should never show a blank
    "System prompt" section for them."""
    profile = get_profile("full")
    for a in list_agents(profile):
        assert a["system_focus"].strip(), (
            f"{a['name']} has empty system_focus — UI would render blank"
        )
