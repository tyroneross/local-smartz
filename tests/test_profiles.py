"""Tests for hardware profile detection."""

from localsmartz.profiles import get_profile, get_model, detect_profile, PROFILES


def test_get_profile_full():
    profile = get_profile("full")
    assert profile["name"] == "full"
    assert "70b" in profile["planning_model"]
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
    assert "70b" in get_model(profile, "planning")
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
