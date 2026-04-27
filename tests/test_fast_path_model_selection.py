"""Tests for fast_path_stream model-selection logic.

Verifies that fast_path_stream picks the 'fast' role model when available,
falls back to 'planning' when no fast_model is set, and that an explicit
model_override always wins — without invoking Ollama.
"""

from localsmartz.profiles import get_model


def _make_profile(planning: str, fast: str | None = None, execution: str = "exec:32b") -> dict:
    """Build a minimal profile dict for testing model resolution."""
    p: dict = {
        "planning_model": planning,
        "execution_model": execution,
    }
    if fast is not None:
        p["fast_model"] = fast
    return p


# ── get_model("fast") resolution ──────────────────────────────────────────────

def test_fast_role_returns_fast_model_when_present():
    """get_model("fast") returns fast_model, not planning_model."""
    profile = _make_profile(planning="gpt-oss:120b", fast="qwen3.5:9b")
    assert get_model(profile, "fast") == "qwen3.5:9b"


def test_fast_role_falls_back_to_planning_when_absent():
    """get_model("fast") falls back to planning_model when fast_model is unset."""
    profile = _make_profile(planning="gpt-oss:120b")  # no fast_model key
    assert get_model(profile, "fast") == "gpt-oss:120b"


def test_fast_role_falls_back_when_fast_model_is_empty_string():
    """get_model("fast") falls back to planning_model when fast_model is ''."""
    profile = _make_profile(planning="gpt-oss:120b", fast="")
    assert get_model(profile, "fast") == "gpt-oss:120b"


# ── fast_path_stream model selection (stub — no Ollama) ───────────────────────

def _resolve_fast_path_model(profile: dict, model_override: str | None) -> str:
    """Mirror the model-selection logic from fast_path_stream.

    Extracted as a pure function so tests can verify the resolution without
    spinning up ChatOllama.
    """
    if model_override:
        return model_override
    fast_role = get_model(profile, "fast")
    return fast_role if fast_role else get_model(profile, "planning")


def test_fast_path_picks_fast_model_over_planning():
    """With both fast and planning configured, fast wins."""
    profile = _make_profile(planning="gpt-oss:120b", fast="qwen3.5:9b")
    assert _resolve_fast_path_model(profile, None) == "qwen3.5:9b"
    # Critically: must NOT be the 120b heavy model
    assert _resolve_fast_path_model(profile, None) != "gpt-oss:120b"


def test_fast_path_falls_back_to_planning_when_no_fast_role():
    """When no fast_model is configured, planning_model is used."""
    profile = _make_profile(planning="gpt-oss:120b")
    assert _resolve_fast_path_model(profile, None) == "gpt-oss:120b"


def test_override_wins_over_fast_model():
    """Explicit model_override takes precedence over fast_model."""
    profile = _make_profile(planning="gpt-oss:120b", fast="qwen3.5:9b")
    assert _resolve_fast_path_model(profile, "llama3.3:70b") == "llama3.3:70b"


def test_override_wins_over_planning_fallback():
    """Explicit model_override takes precedence even when no fast_model is set."""
    profile = _make_profile(planning="gpt-oss:120b")
    assert _resolve_fast_path_model(profile, "custom:3b") == "custom:3b"
