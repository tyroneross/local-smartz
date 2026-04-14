"""Tests for hardware profile detection."""

from localsmartz.profiles import (
    AGENT_ROLES,
    PROFILES,
    agent_tool_names,
    detect_profile,
    get_model,
    get_profile,
)


def test_get_profile_full():
    profile = get_profile("full")
    assert profile["name"] == "full"
    # Planning model is a fast lightweight model for low first-token latency;
    # execution still escalates to a strong coder model.
    assert "8b" in profile["planning_model"]
    assert "32b" in profile["execution_model"]
    assert profile["max_concurrent_agents"] == 2


def test_get_profile_lite():
    profile = get_profile("lite")
    assert profile["name"] == "lite"
    assert "8b" in profile["planning_model"]
    assert profile["max_concurrent_agents"] == 1


def test_get_profile_auto():
    # Should succeed without error (auto-detects)
    profile = get_profile()
    assert profile["name"] in ("full", "lite")


def test_get_model():
    profile = get_profile("full")
    assert "8b" in get_model(profile, "planning")
    assert "32b" in get_model(profile, "execution")


def test_get_model_lite_same():
    profile = get_profile("lite")
    # Lite uses same model for both
    assert get_model(profile, "planning") == get_model(profile, "execution")


def test_detect_profile_returns_valid():
    result = detect_profile()
    assert result in ("full", "lite")


def test_invalid_profile():
    try:
        get_profile("nonexistent")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_invalid_role():
    profile = get_profile("lite")
    try:
        get_model(profile, "invalid")
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


# ── Phase 4: Feature gates ──

def test_full_profile_feature_gates():
    profile = get_profile("full")
    assert profile["max_turns"] == 20
    assert profile["quality_review"] is True
    assert profile["subagent_delegation"] is True


def test_lite_profile_feature_gates():
    profile = get_profile("lite")
    assert profile["max_turns"] == 10
    assert profile["quality_review"] is False
    assert profile["subagent_delegation"] is False


# ── Phase 5: Model override ──

def test_model_override_replaces_planning():
    profile = get_profile("lite", model_override="gemma2:9b")
    assert profile["planning_model"] == "gemma2:9b"
    assert profile["name"] == "lite"


def test_model_override_none_keeps_default():
    profile = get_profile("lite", model_override=None)
    assert profile["planning_model"] == PROFILES["lite"]["planning_model"]


def test_model_override_does_not_affect_execution():
    profile = get_profile("full", model_override="gemma2:9b")
    assert profile["planning_model"] == "gemma2:9b"
    assert profile["execution_model"] == PROFILES["full"]["execution_model"]


def test_model_override_empty_string_keeps_default():
    profile = get_profile("lite", model_override="")
    assert profile["planning_model"] == PROFILES["lite"]["planning_model"]


# ── Evidence contract (Codex finding 4) ──

def test_fact_checker_can_scrape_url():
    """Fact-checker must be able to read full pages, not just search snippets."""
    tools = agent_tool_names("fact_checker")
    assert "scrape_url" in tools
    assert "web_search" in tools  # search still needed to find URLs


def test_researcher_prompt_requires_scrape_before_cite():
    """Researcher must scrape at least one URL before treating a finding as
    confirmed — blocks the 'cite from search snippet' failure mode."""
    prompt = AGENT_ROLES["researcher"]["system_focus"].lower()
    assert "scrape" in prompt
    assert "snippet" in prompt


def test_fact_checker_prompt_mentions_scrape_url():
    """Fact-checker prompt must direct the model to use scrape_url on uncertain
    claims — otherwise the new tool just sits there unused."""
    prompt = AGENT_ROLES["fact_checker"]["system_focus"].lower()
    assert "scrape_url" in prompt


# ── Analyzer contract (Codex finding 3) ──

def test_analyzer_prompt_does_not_claim_prior_research_on_disk():
    """Analyzer runs in parallel with the researcher, so the prompt must not
    tell it to 'read prior research from disk' — that directive misled the
    model into hallucinating missing files."""
    prompt = AGENT_ROLES["analyzer"]["system_focus"].lower()
    assert "prior research" not in prompt or "do not assume" in prompt or "parallel" in prompt
    # Positive assertion: the prompt should mention the parallel-with-researcher
    # constraint so the model knows why no research is on disk yet.
    assert "parallel" in prompt
