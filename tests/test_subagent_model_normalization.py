"""Regression tests for Codex finding 1: subagent model strings must be
normalized to ChatOllama instances before DeepAgents sees them.

The original failure: ``_build_subagent_specs`` passed bare Ollama tag
strings (e.g. ``"qwen3:8b-q4_K_M"``, ``"qwen2.5-coder:32b-instruct-q5_K_M"``)
into DeepAgents' ``subagents=`` list. DeepAgents resolves those via
LangChain's ``init_chat_model``, which cannot infer a provider from a bare
tag and raises ``ValueError("model_provider")`` — crashing every CLI query
before it reached the LLM.

Fix: each subagent's ``model`` field is a prebuilt ``ChatOllama`` instance
(via ``_create_model``), matching the main agent's construction path.

The tests below:
1. Build subagent specs via the representative code path.
2. Assert each ``model`` is a ChatOllama instance (the post-fix contract)
   OR a string that starts with a provider prefix like ``ollama:`` (the
   fallback form the task spec allows).
3. Confirm the raw profile strings would have failed LangChain's
   ``init_chat_model`` resolver — the exact exception we are preventing.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from localsmartz.agent import _build_subagent_specs
from localsmartz.profiles import PROFILES, get_profile


def _normalized_ok(model_value) -> bool:
    """A normalized model field is either a real chat-model instance or a
    provider-prefixed string. Anything else (bare Ollama tag, None) would
    send DeepAgents back down the ``init_chat_model`` path that crashes."""
    # ChatOllama (and any BaseChatModel) counts as normalized.
    from langchain_ollama import ChatOllama

    if isinstance(model_value, ChatOllama):
        return True
    if isinstance(model_value, str):
        # Minimum viable: provider prefix present.
        return ":" in model_value and not model_value.startswith(":") and any(
            model_value.startswith(prefix)
            for prefix in ("ollama:", "openai:", "anthropic:", "google:")
        )
    return False


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Isolate global_config.json writes from the real user home."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def test_subagent_models_are_normalized_for_full_profile(fake_home):
    """Every subagent spec for the ``full`` profile must have a normalized
    ``model`` field — either a ChatOllama instance or a provider-prefixed
    string. Without the fix the field was a bare Ollama tag and DeepAgents
    would raise ``ValueError("model_provider")`` when resolving it."""
    profile = get_profile("full")
    specs = _build_subagent_specs(profile, all_tools=[])
    assert specs, "full profile should produce at least one subagent spec"
    for spec in specs:
        # Every spec in AGENT_ROLES has a configured model in the full profile,
        # so every spec should carry one. A missing ``model`` key would mean
        # DeepAgents falls back to the main agent's model — not the regression
        # we're guarding, but still wrong for this profile.
        assert "model" in spec, f"subagent {spec['name']!r} is missing a model"
        assert _normalized_ok(spec["model"]), (
            f"subagent {spec['name']!r} has un-normalized model "
            f"{spec['model']!r} — DeepAgents will hand this to "
            "init_chat_model and crash"
        )


def test_subagent_models_are_normalized_for_lite_profile(fake_home):
    """Same contract for the lite profile. The lite profile doesn't define
    every role (no fact_checker), so ``model`` may be absent — when it IS
    present, it must still be normalized. A missing ``model`` means
    DeepAgents uses the main agent's model, which is already normalized by
    the main-agent construction path."""
    profile = get_profile("lite")
    specs = _build_subagent_specs(profile, all_tools=[])
    assert specs
    for spec in specs:
        if "model" not in spec:
            continue
        assert _normalized_ok(spec["model"]), (
            f"lite subagent {spec['name']!r} has un-normalized model "
            f"{spec['model']!r}"
        )


def test_bare_ollama_tags_would_fail_init_chat_model():
    """Proof-of-regression: the raw strings living in PROFILES are exactly
    the inputs that would crash DeepAgents' model resolver. If this test
    ever stops raising, the shape of the upstream failure has changed and
    the normalization guard above may need revisiting.
    """
    from langchain.chat_models import init_chat_model

    bare_tag = PROFILES["full"]["agents"]["analyzer"]["model"]
    # Sanity-check the fixture: we want a bare tag, not something already
    # provider-prefixed like "ollama:qwen3:8b-q4_K_M".
    assert ":" in bare_tag and not bare_tag.startswith("ollama:")

    # LangChain's resolver cannot infer the provider from a bare Ollama tag.
    with pytest.raises(Exception) as exc_info:
        init_chat_model(bare_tag)
    # The error message mentions model_provider — this is the exact failure
    # mode Codex flagged.
    assert "model_provider" in str(exc_info.value) or "provider" in str(exc_info.value).lower()


def test_normalized_chatollama_instances_bypass_init_chat_model(fake_home):
    """A ChatOllama instance handed to DeepAgents is used directly — it does
    NOT go through init_chat_model, so the bare-tag failure mode is side-
    stepped. We assert this by checking the instance is truthy and has
    ``bind_tools`` (the API DeepAgents actually calls on the model)."""
    from langchain_ollama import ChatOllama

    profile = get_profile("full")
    specs = _build_subagent_specs(profile, all_tools=[])
    for spec in specs:
        model = spec.get("model")
        # Under the current fix every spec should be a ChatOllama instance.
        # If someone later moves to the string-fallback form, this assertion
        # will flag it and the test can be adjusted intentionally.
        assert isinstance(model, ChatOllama), (
            f"{spec['name']!r} should be a ChatOllama instance after "
            f"normalization; got {type(model).__name__}"
        )
        # ``bind_tools`` is what DeepAgents calls — RunnableRetry doesn't
        # expose it, which is why _create_model refuses to wrap. The bare
        # ChatOllama does.
        assert hasattr(model, "bind_tools")
