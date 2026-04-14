"""Tests for the deterministic LangGraph pipeline (flag-gated path).

Strategy: stub ``_invoke_role`` so no Ollama calls fire. That lets us test
the graph topology + routing decisions without the LLM dependency.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from localsmartz import pipeline
from localsmartz.pipeline import (
    MAX_FACT_CHECK_ITERATIONS,
    NODE_NAMES,
    _after_fact_check,
    _parse_fact_verdict,
    build_graph,
    is_enabled,
)
from localsmartz.profiles import get_profile


# ── Feature flag ────────────────────────────────────────────────────────

def test_is_enabled_false_by_default(monkeypatch):
    monkeypatch.delenv("LOCALSMARTZ_PIPELINE", raising=False)
    assert is_enabled() is False


def test_is_enabled_accepts_graph_and_truthy(monkeypatch):
    for val in ("graph", "1", "true", "yes", "on", "GRAPH", "True"):
        monkeypatch.setenv("LOCALSMARTZ_PIPELINE", val)
        assert is_enabled() is True, f"expected {val!r} to enable"


def test_is_enabled_rejects_unrelated(monkeypatch):
    for val in ("0", "false", "deepagents", "", " "):
        monkeypatch.setenv("LOCALSMARTZ_PIPELINE", val)
        assert is_enabled() is False, f"expected {val!r} to be disabled"


# ── Verdict parsing ─────────────────────────────────────────────────────

def test_parse_verdict_clean_json():
    verdict, missing = _parse_fact_verdict(
        '{"verdict": "needs_more", "missing_facts": ["a", "b"]}'
    )
    assert verdict == "needs_more"
    assert missing == ["a", "b"]


def test_parse_verdict_embedded_in_prose():
    """Small models wrap JSON in prose. The parser extracts the first
    object that has a `verdict` key."""
    text = (
        "Here is my evaluation.\n\n"
        '{"verdict": "ok", "missing_facts": []}\n\n'
        "Let me know if you need more."
    )
    verdict, missing = _parse_fact_verdict(text)
    assert verdict == "ok"
    assert missing == []


def test_parse_verdict_defaults_to_ok_on_garbage():
    """Defer to the writer rather than spin if the fact-checker returns
    malformed output — better to ship an imperfect answer than loop."""
    verdict, missing = _parse_fact_verdict("complete nonsense no json here")
    assert verdict == "ok"
    assert missing == []


def test_parse_verdict_rejects_invalid_enum():
    """Only ``ok`` and ``needs_more`` are accepted; anything else falls
    back to ``ok`` for the same 'don't spin' reason."""
    verdict, _ = _parse_fact_verdict('{"verdict": "maybe", "missing_facts": []}')
    assert verdict == "ok"


# ── Routing edge ────────────────────────────────────────────────────────

def test_after_fact_check_routes_needs_more_to_researcher():
    state = {
        "fact_verdict": "needs_more",
        "fact_check_iterations": 1,
    }
    assert _after_fact_check(state) == "researcher"


def test_after_fact_check_routes_ok_to_writer():
    state = {"fact_verdict": "ok", "fact_check_iterations": 1}
    assert _after_fact_check(state) == "writer"


def test_after_fact_check_hard_caps_at_max_iterations():
    """When budget is exhausted, route to writer even on ``needs_more`` to
    prevent runaway loops."""
    state = {
        "fact_verdict": "needs_more",
        "fact_check_iterations": MAX_FACT_CHECK_ITERATIONS + 1,
    }
    assert _after_fact_check(state) == "writer"


# ── Graph compile ───────────────────────────────────────────────────────

def test_build_graph_has_expected_nodes():
    profile = get_profile("lite")
    graph = build_graph(profile=profile)
    # The compiled graph exposes its node names via .get_graph().nodes
    node_names = set(graph.get_graph().nodes.keys())
    # START and END are added by LangGraph automatically; the named nodes
    # must all be present.
    for name in NODE_NAMES:
        assert name in node_names, f"missing node: {name}"


# ── End-to-end execution (stubbed _invoke_role) ─────────────────────────

class _Script:
    """Scripted responses for stubbed ``_invoke_role``. Maps role name →
    list of responses; each invocation pops the next one. Exposes the
    call log for assertions."""

    def __init__(self, responses: dict[str, list[str]]) -> None:
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, role: str, user_content: str, profile: dict) -> str:
        self.calls.append((role, user_content))
        queue = self.responses.get(role, [])
        if queue:
            return queue.pop(0)
        return f"[default {role} response]"


def test_happy_path_runs_parallel_researcher_analyzer_then_fact_check_then_writer():
    """Fact-checker returns ``ok`` immediately — the graph should run
    researcher + analyzer in parallel, then fact_checker once, then
    writer once, then END."""
    script = _Script({
        "researcher": ["found 10 mountains"],
        "analyzer": ["sum with pandas.sum()"],
        "fact_checker": ['{"verdict": "ok", "missing_facts": []}'],
        "writer": ["The ten tallest mountains total 78,000 m."],
    })
    with patch("localsmartz.pipeline._invoke_role", script):
        result = pipeline.run("top 10 tallest mountains total height")

    assert result["final_answer"].startswith("The ten tallest mountains")
    # Both specialists ran.
    call_roles = [role for role, _ in script.calls]
    assert call_roles.count("researcher") == 1
    assert call_roles.count("analyzer") == 1
    assert call_roles.count("fact_checker") == 1
    assert call_roles.count("writer") == 1


def test_redispatches_when_fact_checker_says_needs_more():
    """First fact-check flags missing data → researcher runs AGAIN with
    the missing_facts as instruction. Second fact-check passes → writer."""
    script = _Script({
        "researcher": ["partial research", "complete research after redo"],
        "analyzer": ["aggregation plan"],
        "fact_checker": [
            '{"verdict": "needs_more", "missing_facts": ["K2 height", "Kangchenjunga height"]}',
            '{"verdict": "ok", "missing_facts": []}',
        ],
        "writer": ["Final answer with all 10 summits."],
    })
    with patch("localsmartz.pipeline._invoke_role", script):
        result = pipeline.run("top 10 mountains")

    call_roles = [role for role, _ in script.calls]
    # Researcher ran twice (initial + re-dispatch).
    assert call_roles.count("researcher") == 2
    assert call_roles.count("fact_checker") == 2
    assert call_roles.count("writer") == 1
    assert result["fact_check_iterations"] == 2

    # The second researcher call must contain the missing_facts from the
    # first fact-check — that's how the re-dispatch carries context.
    second_researcher_call = [
        content for role, content in script.calls if role == "researcher"
    ][1]
    assert "K2 height" in second_researcher_call
    assert "Kangchenjunga height" in second_researcher_call


def test_hard_cap_terminates_at_max_iterations():
    """Fact-checker keeps returning needs_more forever. Graph must
    terminate at MAX_FACT_CHECK_ITERATIONS rather than looping."""
    script = _Script({
        "researcher": ["stubborn research"] * 10,
        "analyzer": ["aggregation plan"] * 10,
        "fact_checker": [
            '{"verdict": "needs_more", "missing_facts": ["something"]}'
        ] * 10,
        "writer": ["best-effort answer with known gaps."],
    })
    with patch("localsmartz.pipeline._invoke_role", script):
        result = pipeline.run("unresolvable query")

    call_roles = [role for role, _ in script.calls]
    # Researcher = initial + MAX (2) = 3 total.
    assert call_roles.count("researcher") == MAX_FACT_CHECK_ITERATIONS + 1
    # Writer ran exactly once at the end.
    assert call_roles.count("writer") == 1
    # Writer fires even though verdict is still needs_more.
    assert result["fact_verdict"] == "needs_more"
    assert result["final_answer"] == "best-effort answer with known gaps."


# ── Profile wiring ──────────────────────────────────────────────────────

def test_role_llm_uses_profile_model(monkeypatch):
    """Each role's ChatOllama must be instantiated with the role's
    configured model, not a hardcoded one."""
    seen_models: list[str] = []

    class FakeChat:
        def __init__(self, **kwargs):
            seen_models.append(kwargs.get("model", ""))

        def invoke(self, messages):
            class _Resp:
                content = "stub"
            return _Resp()

    monkeypatch.setattr("localsmartz.pipeline.ChatOllama", FakeChat)
    from localsmartz.pipeline import _invoke_role

    profile = get_profile("full")
    _invoke_role("writer", "hello", profile)
    assert seen_models, "ChatOllama was not instantiated"
    # Writer on full profile uses the execution model (32b coder).
    assert "32b" in seen_models[-1] or "coder" in seen_models[-1].lower()
