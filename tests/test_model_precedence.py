"""Precedence matrix for the single-model-mode pin (work item 2).

Frozen contract: CLI --model (per-run) > active_model (global_config) >
serve select/project planning_model (planning) / agent_models[role]
(roles) > profile defaults.

``global_pinned_model()`` is the one helper every touch point consults
(profiles.get_model, profiles.get_agent_model, profiles.effective_agent_models,
profiles.list_agents, agent._create_model, pipeline._role_llm) — these tests
exercise it directly plus each downstream consumer.
"""
from __future__ import annotations

import pytest

from localsmartz import global_config
from localsmartz.profiles import (
    PROFILES,
    effective_agent_models,
    get_agent_model,
    get_model,
    get_profile,
    global_pinned_model,
    list_agents,
)


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


# ── global_pinned_model() ─────────────────────────────────────────────────

def test_global_pinned_model_none_when_unset():
    assert global_pinned_model() is None


def test_global_pinned_model_none_when_blank():
    global_config.set("active_model", "   ")
    assert global_pinned_model() is None


def test_global_pinned_model_returns_stripped_value():
    global_config.set("active_model", "  qwen3:8b  ")
    assert global_pinned_model() == "qwen3:8b"


# ── get_model precedence ──────────────────────────────────────────────────

def test_get_model_uses_profile_default_when_nothing_pinned():
    profile = get_profile("full")
    assert get_model(profile, "planning") == PROFILES["full"]["planning_model"]
    assert get_model(profile, "execution") == PROFILES["full"]["execution_model"]


def test_get_model_active_model_pin_wins_over_profile_default():
    global_config.set("active_model", "pinned:model")
    profile = get_profile("full")
    assert get_model(profile, "planning") == "pinned:model"
    assert get_model(profile, "execution") == "pinned:model"
    assert get_model(profile, "fast") == "pinned:model"


def test_get_model_cli_pin_wins_over_active_model():
    global_config.set("active_model", "global:pin")
    profile = get_profile("full", model_override="cli:pin", cli_pin=True)
    assert get_model(profile, "planning") == "cli:pin"
    assert get_model(profile, "execution") == "cli:pin"


def test_get_model_non_cli_model_override_does_not_beat_active_model():
    """A model_override that ISN'T a genuine CLI pin (cli_pin=False, the
    default — e.g. serve.py's saved project config) sits BELOW active_model
    per the frozen precedence."""
    global_config.set("active_model", "global:pin")
    profile = get_profile("full", model_override="project:config-value")
    assert get_model(profile, "planning") == "global:pin"


# ── get_agent_model / effective_agent_models / list_agents precedence ────

def test_get_agent_model_active_model_pin_wins_over_agent_override():
    global_config.set("agent_models", {"analyzer": "custom:override"})
    global_config.set("active_model", "global:pin")
    profile = get_profile("full")
    assert get_agent_model(profile, "analyzer") == "global:pin"


def test_get_agent_model_agent_override_wins_when_no_pin():
    global_config.set("agent_models", {"analyzer": "custom:override"})
    profile = get_profile("full")
    assert get_agent_model(profile, "analyzer") == "custom:override"


def test_get_agent_model_cli_pin_wins_over_everything():
    global_config.set("agent_models", {"analyzer": "custom:override"})
    global_config.set("active_model", "global:pin")
    profile = get_profile("full", model_override="cli:pin", cli_pin=True)
    assert get_agent_model(profile, "analyzer") == "cli:pin"


def test_effective_agent_models_reports_pin():
    global_config.set("active_model", "global:pin")
    profile = get_profile("full")
    eff = effective_agent_models(profile)
    assert all(v == "global:pin" for v in eff.values())


def test_list_agents_reports_pin_as_model():
    global_config.set("active_model", "global:pin")
    profile = get_profile("full")
    by_name = {a["name"]: a for a in list_agents(profile)}
    assert by_name["analyzer"]["model"] == "global:pin"
    assert by_name["writer"]["model"] == "global:pin"


# ── get_profile's cli_pin stash ───────────────────────────────────────────

def test_get_profile_no_stash_without_cli_pin():
    profile = get_profile("full", model_override="something")
    assert profile.get("_cli_pinned_model") is None


def test_get_profile_stashes_when_cli_pin_true():
    profile = get_profile("full", model_override="something", cli_pin=True)
    assert profile.get("_cli_pinned_model") == "something"


def test_get_profile_no_stash_when_cli_pin_true_but_no_override():
    profile = get_profile("full", cli_pin=True)
    assert profile.get("_cli_pinned_model") is None
