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
    assert len(agents) == 5
    names = {a["name"] for a in agents}
    assert names == {"planner", "researcher", "analyzer", "writer", "reviewer"}
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

    # The agent_focus_prompt section should have been woven into the system prompt.
    call_kwargs = mock_create.call_args.kwargs
    sys_prompt = call_kwargs.get("system_prompt", "")
    assert "Single-Agent Mode" in sys_prompt
    assert "ANALYZER agent" in sys_prompt


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
