"""Tests for runners.factory.create_langchain_model (S1 refactor, Phase 3).

Locks in the invariants that matter:

- ollama path returns a ChatOllama with no ``.with_retry`` wrapping.
- qwen3.5 family auto-injects ``reasoning: false`` (F22 guard — delegates
  to runners.local_ollama._should_disable_reasoning, the single source
  of truth).
- anthropic path lazy-imports and returns a ChatAnthropic.
- openai/groq path swaps ``base_url`` correctly for groq.
"""
from __future__ import annotations

import pytest


def test_ollama_plain_model_no_reasoning_kwarg(monkeypatch):
    """A non-qwen3.5 model must NOT inject reasoning=False."""
    from localsmartz.runners import factory

    # Force registry lookup to return a non-reasoning-off rec.
    monkeypatch.setattr(
        "localsmartz.runners.local_ollama._should_disable_reasoning",
        lambda name: False,
    )

    llm = factory.create_langchain_model("ollama", {"name": "llama3.2:3b"})
    assert llm.model == "llama3.2:3b"
    # ChatOllama's ``reasoning`` field should NOT be set to False for a
    # non-qwen3.5 model. (``None`` is fine; ``False`` would indicate
    # F22 guard misfired.)
    dump = llm.model_dump()
    assert dump.get("reasoning") is not False, (
        f"plain ollama model must not inject reasoning=False; got dump={dump!r}"
    )


def test_ollama_qwen35_injects_reasoning_false(monkeypatch):
    """qwen3.5 family must auto-inject reasoning=False (F22)."""
    from localsmartz.runners import factory

    monkeypatch.setattr(
        "localsmartz.runners.local_ollama._should_disable_reasoning",
        lambda name: name.startswith("qwen3.5"),
    )

    llm = factory.create_langchain_model("ollama", {"name": "qwen3.5:9b"})
    # ChatOllama in current langchain-ollama exposes ``reasoning`` as a
    # top-level field; older versions used ``model_kwargs``. We set both
    # so this assertion works across the supported range.
    dump = llm.model_dump()
    assert dump.get("reasoning") is False, (
        f"qwen3.5 model must inject reasoning=False; got dump={dump!r}"
    )


def test_anthropic_path_returns_chatanthropic(monkeypatch):
    """anthropic provider returns a ChatAnthropic when a key is available."""
    from localsmartz.runners import factory

    # Pydantic validates api_key at construction — supply a placeholder.
    monkeypatch.setattr(factory, "_cloud_api_key", lambda p: "test-key")
    llm = factory.create_langchain_model(
        "anthropic", {"name": "claude-sonnet-4-5-20250929"}
    )
    assert llm.__class__.__name__ == "ChatAnthropic"


def test_groq_path_swaps_base_url(monkeypatch):
    """groq provider sets the OpenAI-compat base_url when not overridden."""
    from localsmartz.runners import factory

    monkeypatch.setattr(factory, "_cloud_api_key", lambda p: "test-key")
    llm = factory.create_langchain_model("groq", {"name": "llama-3.3-70b-versatile"})
    assert llm.__class__.__name__ == "ChatOpenAI"
    # ChatOpenAI stores base_url on the client. Inspect via the public attr.
    base_url = str(getattr(llm, "openai_api_base", "") or getattr(llm, "base_url", ""))
    assert "groq.com" in base_url, (
        f"groq path should route to groq.com; got base_url={base_url!r}"
    )


def test_openai_path_no_base_url_override(monkeypatch):
    """openai provider uses the SDK default base_url unless explicitly set."""
    from localsmartz.runners import factory

    monkeypatch.setattr(factory, "_cloud_api_key", lambda p: "test-key")
    llm = factory.create_langchain_model("openai", {"name": "gpt-4o-mini"})
    assert llm.__class__.__name__ == "ChatOpenAI"


def test_unknown_provider_falls_back_to_ollama(monkeypatch):
    """Unknown provider strings must not crash — fall back to ollama."""
    from localsmartz.runners import factory

    monkeypatch.setattr(
        "localsmartz.runners.local_ollama._should_disable_reasoning",
        lambda name: False,
    )
    llm = factory.create_langchain_model("antrohpic-typo", {"name": "llama3.2:3b"})
    assert llm.__class__.__name__ == "ChatOllama"


def test_missing_name_raises():
    """A model_ref without ``name`` must surface a clear error."""
    from localsmartz.runners import factory

    with pytest.raises(ValueError, match="name"):
        factory.create_langchain_model("ollama", {})


def test_factory_no_with_retry_in_source():
    """Static guard: the factory must never wrap in with_retry (DeepAgents gotcha).

    Scans only non-docstring, non-comment lines — the module's docstring
    legitimately mentions the bug we're guarding against.
    """
    from pathlib import Path
    import localsmartz.runners.factory as mod

    src_lines = Path(mod.__file__).read_text().splitlines()
    offenders: list[tuple[int, str]] = []
    in_triple = False
    for i, raw in enumerate(src_lines, start=1):
        line = raw.strip()
        # Toggle triple-quote state. Works for single- and double-tripled.
        if '"""' in line or "'''" in line:
            # Flip toggle for each occurrence.
            count = line.count('"""') + line.count("'''")
            if count % 2 == 1:
                in_triple = not in_triple
            continue
        if in_triple:
            continue
        if line.startswith("#"):
            continue
        if ".with_retry(" in line:
            offenders.append((i, raw))

    assert not offenders, (
        "runners/factory.py contains a .with_retry( call outside docstrings — "
        "this breaks create_deep_agent's model cache. "
        "See reference_deepagents_runnable_retry.md. "
        f"Offending lines: {offenders}"
    )
