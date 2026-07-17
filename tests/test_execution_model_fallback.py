"""Execution/agent-role model fallback (work item 7b): agent._create_ollama_model
must apply an availability check + resolve_available_model fallback with a
stderr warning — mirroring what config.resolve_model already does for the
planning model — so a missing tag degrades instead of 404ing mid-run."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from localsmartz import agent as agent_mod


def _fake_chat_ollama():
    seen = {}

    class FakeChatOllama:
        def __init__(self, **kwargs):
            seen["kwargs"] = kwargs
            self.kwargs = kwargs

    return FakeChatOllama, seen


def test_execution_model_falls_back_when_tag_missing(capsys):
    FakeChatOllama, seen = _fake_chat_ollama()
    with patch("localsmartz.agent.ChatOllama", FakeChatOllama), \
         patch(
             "localsmartz.ollama.resolve_available_model",
             return_value=("qwen3:8b-q4_K_M", "Model 'missing:tag' not pulled — using 'qwen3:8b-q4_K_M' instead."),
         ):
        model = agent_mod._create_ollama_model("missing:tag")

    assert isinstance(model, FakeChatOllama)
    assert seen["kwargs"]["model"] == "qwen3:8b-q4_K_M"
    err = capsys.readouterr().err
    assert "Warning" in err
    assert "missing:tag" in err
    assert "qwen3:8b-q4_K_M" in err


def test_execution_model_no_warning_when_tag_installed(capsys):
    FakeChatOllama, seen = _fake_chat_ollama()
    with patch("localsmartz.agent.ChatOllama", FakeChatOllama), \
         patch("localsmartz.ollama.resolve_available_model", return_value=("installed:tag", None)):
        model = agent_mod._create_ollama_model("installed:tag")

    assert seen["kwargs"]["model"] == "installed:tag"
    assert capsys.readouterr().err == ""


def test_execution_model_ollama_down_warns_but_keeps_original_name(capsys):
    """When Ollama itself is unreachable, resolve_available_model returns
    (None, error) — the degrade path must not crash construction (the
    downstream ChatOllama call will surface the real connection error);
    it should still warn."""
    FakeChatOllama, seen = _fake_chat_ollama()
    with patch("localsmartz.agent.ChatOllama", FakeChatOllama), \
         patch(
             "localsmartz.ollama.resolve_available_model",
             return_value=(None, "Ollama is not running. Start it with: ollama serve"),
         ):
        model = agent_mod._create_ollama_model("some:tag")

    assert seen["kwargs"]["model"] == "some:tag"
    err = capsys.readouterr().err
    assert "not running" in err.lower()


def test_availability_check_failure_never_crashes_construction(capsys):
    """If the availability check itself blows up (e.g. an unexpected
    exception inside resolve_available_model), model construction must
    still proceed with the original name rather than propagating."""
    FakeChatOllama, seen = _fake_chat_ollama()
    with patch("localsmartz.agent.ChatOllama", FakeChatOllama), \
         patch("localsmartz.ollama.resolve_available_model", side_effect=RuntimeError("boom")):
        model = agent_mod._create_ollama_model("some:tag")

    assert isinstance(model, FakeChatOllama)
    assert seen["kwargs"]["model"] == "some:tag"


def test_subagent_spec_model_goes_through_fallback(capsys):
    """_build_subagent_specs (which every role's model construction routes
    through via _create_model) inherits the same fallback — proves the
    fix applies to agent-role models, not just planning."""
    from localsmartz.agent import _build_subagent_specs
    from localsmartz.profiles import get_profile

    FakeChatOllama, seen = _fake_chat_ollama()
    with patch("localsmartz.agent.ChatOllama", FakeChatOllama), \
         patch(
             "localsmartz.ollama.resolve_available_model",
             return_value=("fallback:model", "not pulled — using fallback:model instead"),
         ):
        profile = get_profile("full")
        specs = _build_subagent_specs(profile, all_tools=[])

    by_name = {s["name"]: s for s in specs}
    assert by_name["analyzer"]["model"].kwargs["model"] == "fallback:model"
    err = capsys.readouterr().err
    assert "fallback:model" in err
