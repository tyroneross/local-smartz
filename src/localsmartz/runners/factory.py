"""LangChain chat-model factory shared by ``agent.py`` and ``patterns/``.

This is the S1 refactor from Phase 3. Patterns that need a raw LangChain
chat model (e.g. orchestrator's DeepAgents Path A) can reach in here
without importing ``agent.py`` (which pulls the full DeepAgents stack).

Key invariants (DO NOT BREAK):

- **No ``.with_retry()`` wrapping here.** See project memory
  ``reference_deepagents_runnable_retry.md``: ``RunnableRetry`` is
  unhashable inside ``create_deep_agent``'s model cache and strips
  ``bind_tools``. Callers that need retries must wrap *after* DeepAgents
  construction or handle retry at the runner-turn level.
- **qwen3.5 auto ``reasoning: false`` injection preserved.** We delegate
  to the single source of truth in ``runners.local_ollama._should_disable_reasoning``
  so the flag can't drift between the runner path and this factory.
- **Cloud deps are lazy-imported.** Callers with ollama-only setups must
  not pay the cloud SDK import cost.

Minimal surface: one function, ``create_langchain_model(provider, model_ref)``.
Exported at package level via ``runners.__init__``.
"""
from __future__ import annotations

import os
from typing import Any


def create_langchain_model(provider: str, model_ref: dict) -> Any:
    """Build a bare LangChain chat model for ``provider`` + ``model_ref``.

    Args:
        provider: ``"ollama"`` | ``"anthropic"`` | ``"openai"`` | ``"groq"``.
            Unknown strings fall back to ollama — the agent graph must
            never crash on a stray config key (matches ``agent.py::_active_provider``
            semantics).
        model_ref: ``{"name": str, "base_url"?: str, ...}``. The ``name``
            is the provider-specific model identifier.

    Returns:
        An unwrapped LangChain chat model. The caller is responsible for
        ``.bind_tools()`` / ``.ainvoke()`` etc. Never wrapped in
        ``with_retry``.
    """
    name = (model_ref or {}).get("name", "")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(
            f"create_langchain_model: model_ref missing 'name': {model_ref!r}"
        )
    name = name.strip()
    provider_norm = (provider or "ollama").strip().lower()

    if provider_norm == "ollama":
        return _create_ollama(name)
    if provider_norm == "anthropic":
        return _create_anthropic(name)
    if provider_norm in ("openai", "groq"):
        base_url = (model_ref or {}).get("base_url")
        return _create_openai_compat(name, provider=provider_norm, base_url=base_url)
    # Unknown provider → ollama fallback. Same forgiving behavior as
    # agent.py::_create_model.
    return _create_ollama(name)


def _create_ollama(name: str) -> Any:
    """Bare ``ChatOllama`` with qwen3.5 ``reasoning: false`` auto-injection."""
    import httpx  # local — only needed for the timeout struct
    from langchain_ollama import ChatOllama

    # Single source of truth for the F22 registry flag.
    from localsmartz.runners.local_ollama import _should_disable_reasoning

    kwargs: dict[str, Any] = {
        "model": name,
        "temperature": 0,
        "num_ctx": 4096,
        "client_kwargs": {
            "timeout": httpx.Timeout(
                connect=5.0, read=600.0, write=30.0, pool=5.0
            ),
        },
    }
    # Prefer the top-level ``reasoning`` field (langchain-ollama >= 0.3
    # exposes this directly). Keep ``model_kwargs`` as a parallel channel
    # for older langchain-ollama versions that forwarded model_kwargs to
    # the Ollama generate payload.
    if _should_disable_reasoning(name):
        kwargs["reasoning"] = False
        kwargs["model_kwargs"] = {"reasoning": False}

    return ChatOllama(**kwargs)


def _create_anthropic(name: str) -> Any:
    """Bare ``ChatAnthropic`` with key resolved from secrets/env."""
    from langchain_anthropic import ChatAnthropic  # lazy — cloud path only

    api_key = _cloud_api_key("anthropic")
    return ChatAnthropic(
        model=name,
        temperature=0,
        api_key=api_key,
        timeout=600.0,
    )


def _create_openai_compat(name: str, *, provider: str, base_url: str | None = None) -> Any:
    """Bare ``ChatOpenAI`` for openai + groq.

    ``provider`` is ``openai`` or ``groq``. Groq uses the OpenAI-compatible
    endpoint at ``https://api.groq.com/openai/v1`` unless ``base_url`` is
    explicitly provided in the model_ref.
    """
    from langchain_openai import ChatOpenAI  # lazy — cloud path only

    api_key = _cloud_api_key(provider)
    kwargs: dict[str, Any] = {
        "model": name,
        "temperature": 0,
        "api_key": api_key,
        "timeout": 600.0,
    }
    effective_base_url = base_url
    if provider == "groq" and not effective_base_url:
        effective_base_url = "https://api.groq.com/openai/v1"
    if effective_base_url:
        kwargs["base_url"] = effective_base_url
    return ChatOpenAI(**kwargs)


def _cloud_api_key(provider: str) -> str | None:
    """Fetch a cloud provider API key. Keychain first, env-var fallback.

    Mirrors ``agent.py::_cloud_api_key`` so both paths read from the same
    source of truth. Returns ``None`` when no key is available — the
    downstream SDK surfaces the auth error to the UI.
    """
    try:
        from localsmartz import secrets as _secrets

        val = _secrets.get(f"{provider}_api_key")
        if isinstance(val, str) and val.strip():
            return val.strip()
    except Exception:  # noqa: BLE001
        pass
    env = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "groq": "GROQ_API_KEY",
    }.get(provider)
    if env:
        val = os.environ.get(env)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


__all__ = ["create_langchain_model"]
