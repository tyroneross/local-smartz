"""Tests for hardware profile detection."""

from localsmartz.profiles import get_profile, get_model, detect_profile, PROFILES


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
