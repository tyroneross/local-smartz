"""disabled_agents filtering (work item 3): subagent specs, pipeline graph
topology, list_agents enabled flags, and the validation guards."""
from __future__ import annotations

import pytest

from localsmartz import global_config, pipeline
from localsmartz.agent import _build_subagent_specs
from localsmartz.profiles import (
    RUNNABLE_ROLES,
    get_profile,
    is_agent_disabled,
    list_agents,
    validate_disabled_agents,
)


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


# ── validate_disabled_agents guard rules ──────────────────────────────────

def test_validate_rejects_unknown_role():
    assert validate_disabled_agents(["ghost"]) is not None


def test_validate_rejects_orchestrator():
    assert validate_disabled_agents(["orchestrator"]) is not None


def test_validate_rejects_all_runnable_disabled():
    assert validate_disabled_agents(list(RUNNABLE_ROLES)) is not None


def test_validate_accepts_partial_disable():
    assert validate_disabled_agents(["analyzer"]) is None


def test_validate_rejects_non_list():
    assert validate_disabled_agents("analyzer") is not None


def test_validate_accepts_empty_list():
    assert validate_disabled_agents([]) is None


# ── is_agent_disabled ──────────────────────────────────────────────────────

def test_is_agent_disabled_reflects_global_config():
    assert is_agent_disabled("analyzer") is False
    global_config.set("disabled_agents", ["analyzer"])
    assert is_agent_disabled("analyzer") is True
    assert is_agent_disabled("researcher") is False


def test_orchestrator_never_disableable_even_if_persisted():
    """Defensive: even if something bypassed validation and wrote
    'orchestrator' into disabled_agents, is_agent_disabled must still
    report False for it — it's a structural, not a settable, guarantee."""
    global_config.save_global({"disabled_agents": ["orchestrator"]})
    assert is_agent_disabled("orchestrator") is False


# ── list_agents "enabled" flags ───────────────────────────────────────────

def test_list_agents_enabled_true_by_default():
    profile = get_profile("full")
    for a in list_agents(profile):
        assert a["enabled"] is True


def test_list_agents_enabled_false_for_disabled_role():
    global_config.set("disabled_agents", ["analyzer"])
    profile = get_profile("full")
    by_name = {a["name"]: a for a in list_agents(profile)}
    assert by_name["analyzer"]["enabled"] is False
    assert by_name["writer"]["enabled"] is True


# ── _build_subagent_specs filtering ────────────────────────────────────────

def test_build_subagent_specs_excludes_disabled_role():
    global_config.set("disabled_agents", ["analyzer"])
    profile = get_profile("full")
    specs = _build_subagent_specs(profile, all_tools=[])
    names = {s["name"] for s in specs}
    assert "analyzer" not in names
    assert "researcher" in names


def test_build_subagent_specs_includes_all_when_nothing_disabled():
    profile = get_profile("full")
    specs = _build_subagent_specs(profile, all_tools=[])
    names = {s["name"] for s in specs}
    assert names == {"planner", "researcher", "analyzer", "writer", "fact_checker"}


# ── pipeline graph topology ────────────────────────────────────────────────

def test_graph_skips_node_for_disabled_analyzer():
    global_config.set("disabled_agents", ["analyzer"])
    profile = get_profile("lite")
    graph = pipeline.build_graph(profile=profile)
    node_names = set(graph.get_graph().nodes.keys())
    assert "analyzer" not in node_names
    assert "researcher" in node_names
    assert "fact_checker" in node_names
    assert "writer" in node_names


def test_graph_skips_fact_checker_verdict_loop_when_disabled():
    global_config.set("disabled_agents", ["fact_checker"])
    profile = get_profile("lite")
    graph = pipeline.build_graph(profile=profile)
    node_names = set(graph.get_graph().nodes.keys())
    assert "fact_checker" not in node_names
    assert "researcher" in node_names
    assert "analyzer" in node_names
    assert "writer" in node_names


def test_graph_has_all_nodes_when_nothing_disabled():
    profile = get_profile("lite")
    graph = pipeline.build_graph(profile=profile)
    node_names = set(graph.get_graph().nodes.keys())
    for name in pipeline.NODE_NAMES:
        assert name in node_names


def test_after_fact_check_researcher_disabled_never_redispatches():
    """researcher_enabled=False must route to writer even on needs_more —
    there's no specialist left to send the missing_facts back to."""
    state = {"fact_verdict": "needs_more", "fact_check_iterations": 1}
    assert pipeline._after_fact_check(state, researcher_enabled=False) == "writer"
    # Default (researcher enabled) keeps prior behavior.
    assert pipeline._after_fact_check(state, researcher_enabled=True) == "researcher"
    assert pipeline._after_fact_check(state) == "researcher"


def test_end_to_end_analyzer_disabled_researcher_output_reaches_writer(monkeypatch):
    """Full run with analyzer disabled: researcher's output must reach the
    writer (via fact_checker) with no analyzer call at all."""
    global_config.set("disabled_agents", ["analyzer"])
    calls: list[str] = []

    def fake_invoke_role(role, user_content, profile):
        calls.append(role)
        if role == "fact_checker":
            return '{"verdict": "ok", "missing_facts": []}'
        if role == "writer":
            return "final synthesis"
        return f"[{role} output]"

    monkeypatch.setattr(pipeline, "_invoke_role", fake_invoke_role)
    result = pipeline.run("some query", profile=get_profile("lite"))
    assert "analyzer" not in calls
    assert calls.count("researcher") == 1
    assert calls.count("fact_checker") == 1
    assert calls.count("writer") == 1
    assert result["final_answer"] == "final synthesis"


# ── orchestrator roster suffix (agent.py prompt assembly) ─────────────────

def test_orchestrator_roster_suffix_reflects_disabled_agents(tmp_path):
    from unittest.mock import MagicMock, patch

    global_config.set("disabled_agents", ["analyzer"])
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
    assert "Available roles this run:" in main_prompt
    assert "analyzer" not in main_prompt.split("Available roles this run:")[1]
    assert "researcher" in main_prompt
