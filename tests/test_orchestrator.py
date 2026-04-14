"""Tests for the orchestrator role + reshaped fact_checker + pyramid writer.

These lock in the contract that the orchestrator-routed pipeline depends on:
- orchestrator runs as the MAIN agent, not a subagent
- fact_checker is present in the subagent specs with web_search tool
- writer.system_focus embeds pyramid-principle short-form guidance
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from localsmartz.profiles import AGENT_ROLES, agent_tool_names, get_profile, list_agents


# ── AGENT_ROLES shape ───────────────────────────────────────────────────

def test_orchestrator_role_present():
    """The orchestrator role must exist — it's the main-agent prompt for
    the default multi-agent path."""
    assert "orchestrator" in AGENT_ROLES
    meta = AGENT_ROLES["orchestrator"]
    # Orchestrator has no custom tools of its own — it only routes via task().
    # Any tool in its list would let qwen3:8b try to do inline work instead
    # of delegating, which reopens the hallucination surface.
    assert meta.get("tools") == []


def test_orchestrator_system_focus_mentions_task_delegation():
    """The orchestrator prompt must instruct the LLM to use task()."""
    focus = AGENT_ROLES["orchestrator"]["system_focus"].lower()
    assert "task(" in focus or "task " in focus
    # Mentions the three specialists the user's example uses.
    assert "researcher" in focus
    assert "fact_checker" in focus
    assert "writer" in focus


def test_orchestrator_system_focus_mentions_parallel_fanout():
    """Orchestrator must tell the model to fan out via multiple task() calls
    in one turn — this is the only way to get parallel execution from
    DeepAgents without a custom graph."""
    focus = AGENT_ROLES["orchestrator"]["system_focus"].lower()
    # "SAME turn" / "multiple" / "parallel" — any phrasing that implies fanout
    assert any(hint in focus for hint in ("same turn", "multiple task", "parallel"))


def test_orchestrator_prompt_budget_tight_for_small_models():
    """The orchestrator prompt runs on the main planning model (potentially
    qwen3:8b). Keep it under ~900 chars so the model's tool-call window
    doesn't collapse. Empirical ceiling: prompts > 1000 chars trigger the
    `repo_browser.*` hallucinations we fought earlier."""
    focus = AGENT_ROLES["orchestrator"]["system_focus"]
    assert len(focus) < 1000, f"Orchestrator prompt is {len(focus)} chars — shrink it"


# ── Fact-checker (reshaped from reviewer) ────────────────────────────────

def test_fact_checker_role_present():
    """Reviewer was reshaped into fact_checker — reviewer should NOT be in
    AGENT_ROLES anymore. If a test relies on `reviewer` as a role name it
    needs updating."""
    assert "fact_checker" in AGENT_ROLES
    assert "reviewer" not in AGENT_ROLES


def test_fact_checker_has_web_search():
    """Fact-checker needs web_search so it can spot-verify claims rather
    than trusting prior research blindly."""
    tools = agent_tool_names("fact_checker")
    assert "web_search" in tools
    assert "read_file" in tools


def test_fact_checker_system_focus_demands_json_verdict():
    """The fact-checker's contract is a JSON verdict — the orchestrator's
    re-dispatch logic reads `verdict` and `missing_facts` from the return."""
    focus = AGENT_ROLES["fact_checker"]["system_focus"].lower()
    assert "json" in focus
    assert "verdict" in focus
    assert "missing_facts" in focus
    assert "needs_more" in focus


# ── Writer pyramid-principle ─────────────────────────────────────────────

def test_writer_system_focus_contains_pyramid_guidance():
    """Writer must carry pyramid-principle short-form guidance in the prompt.
    Claude Code skills can't be invoked mid-turn from a Python agent —
    the technique is encoded in the role's system_focus directly."""
    focus = AGENT_ROLES["writer"]["system_focus"].lower()
    # Any of these keywords confirms pyramid-principle encoding.
    assert "governing thought" in focus or "governing" in focus
    assert "key line" in focus or "mece" in focus
    assert "support" in focus


def test_writer_still_has_report_tools():
    """Pyramid guidance can't replace the writer's tool budget."""
    tools = agent_tool_names("writer")
    assert "create_report" in tools
    assert "read_file" in tools


# ── list_agents / PROFILES integration ───────────────────────────────────

def test_full_profile_lists_five_pickable_agents_without_orchestrator(fake_home):
    """list_agents FILTERS OUT main-agent-only roles like orchestrator.
    The UI sidebar only shows specialists the user can reasonably focus
    on — pinning the orchestrator would scope the main agent's tools to
    its empty allow-list and lock out delegation.

    Regression guard for the bug we hit in live UI validation where the
    sidebar rendered 'Orchestrator' as a clickable row and picking it
    produced an infinite 'Thinking…' with no output."""
    profile = get_profile("full")
    agents = list_agents(profile)
    names = {a["name"] for a in agents}
    assert names == {
        "planner",
        "researcher",
        "analyzer",
        "writer",
        "fact_checker",
    }
    assert "orchestrator" not in names


def test_focus_agent_orchestrator_is_coerced_to_none(fake_home, tmp_path, monkeypatch):
    """If a client does pass focus_agent='orchestrator' (old UI cache,
    stale config), create_agent must coerce it to None so the main agent
    keeps its full tool set + subagent delegation — NOT land in focus
    mode with an empty allow-list."""
    from unittest.mock import MagicMock, patch
    with patch("localsmartz.agent.ChatOllama") as mock_chat, \
         patch("localsmartz.agent.create_deep_agent") as mock_create:
        mock_chat.return_value = MagicMock()
        mock_create.return_value = MagicMock()
        from localsmartz.agent import create_agent
        create_agent(
            profile_name="full",
            cwd=tmp_path,
            focus_agent="orchestrator",  # should be coerced to None
            include_plugin_skills=False,
            include_plugin_tools=False,
        )

    # The multi-agent branch passes subagents=[...]; focus mode passes
    # subagents=[]. Orchestrator should land in multi-agent.
    call_kwargs = mock_create.call_args.kwargs
    subagents = call_kwargs.get("subagents")
    assert subagents is not None and len(subagents) > 0, (
        "focus_agent='orchestrator' must coerce to multi-agent mode, "
        "not focus mode with empty subagents"
    )


# ── Subagent spec exclusion ──────────────────────────────────────────────

def test_orchestrator_is_not_in_subagent_specs(fake_home, tmp_path):
    """The orchestrator runs as the MAIN agent. It must NOT appear in the
    ``subagents=`` list passed to create_deep_agent — that would create a
    recursive delegation path where the main agent could ``task("orchestrator", ...)``."""
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
    assert "orchestrator" not in by_name
    # The main-agent system prompt should be the orchestrator's routing
    # table, not the generic SYSTEM_PROMPT.
    main_prompt = call_kwargs.get("system_prompt", "")
    assert "ORCHESTRATOR" in main_prompt or "orchestrator" in main_prompt.lower()


def test_fact_checker_appears_in_subagent_specs(fake_home, tmp_path):
    """fact_checker must be reachable via task("fact_checker", ...) from the
    orchestrator — that means it must be registered as a subagent spec."""
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
    assert "fact_checker" in by_name
    # fact_checker's tool names should include web_search (not a
    # BaseTool lookup — just verify the scoped list isn't empty).
    fc_tools = by_name["fact_checker"]["tools"]
    tool_names = {getattr(t, "name", None) or getattr(t, "__name__", None) for t in fc_tools}
    assert "web_search" in tool_names


# ── fake_home fixture (same idiom as test_agent_routing.py) ────────────

@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Redirect HOME so global_config writes land in tmp."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path
