"""AgentRunner protocol + concrete runners (local Ollama, cloud Anthropic, cloud OpenAI-compat).

Patterns (src/localsmartz/patterns/) consume ``AgentRunner`` and never import
ollama/anthropic/openai directly. Model references are provider-tagged:

    ModelRef = {"provider": "ollama" | "anthropic" | "openai" | "groq",
                "name": "qwen3.5:9b-q4_K_M" | "claude-sonnet-4-6" | ...,
                "base_url"?: "https://api.groq.com/openai/v1"}

Export surface:
- AgentRunner (Protocol)
- AssistantTurn (TypedDict)
- ModelRef (TypedDict)
- LocalOllamaRunner
- CloudAnthropicRunner
- CloudOpenAICompatRunner
- get_runner(provider) -> AgentRunner
"""
from __future__ import annotations

import sys

from localsmartz.runners.base import (
    AgentRunner,
    AssistantTurn,
    LocalOnlyError,
    ModelRef,
)
from localsmartz.runners.factory import create_langchain_model
from localsmartz.runners.local_ollama import LocalOllamaRunner

try:  # Cloud deps optional — import guards for offline installs.
    from localsmartz.runners.cloud_anthropic import CloudAnthropicRunner
except ImportError:  # pragma: no cover
    CloudAnthropicRunner = None  # type: ignore[assignment]

try:
    from localsmartz.runners.cloud_openai_compat import CloudOpenAICompatRunner
except ImportError:  # pragma: no cover
    CloudOpenAICompatRunner = None  # type: ignore[assignment]


def _local_only_enabled() -> bool:
    """Read ``global_config.local_only``. Fails CLOSED (cloud denied) when
    ~/.localsmartz/global.json exists but can't be parsed/read — this is a
    privacy boundary, so a corrupt config must never silently re-open cloud
    access. A missing file (fresh install) is not degraded and reads as
    local_only=False, same as the schema default."""
    try:
        from localsmartz import global_config

        value, degraded = global_config.local_only_state()
        if degraded:
            print(
                "Warning: could not read Local-Only setting — failing "
                "closed, cloud providers disabled: global.json exists but "
                "could not be parsed",
                file=sys.stderr,
            )
            return True
        return value
    except Exception as exc:  # noqa: BLE001
        print(
            f"Warning: could not read Local-Only setting — failing closed, "
            f"cloud providers disabled: {exc}",
            file=sys.stderr,
        )
        return True


def get_runner(provider: str) -> AgentRunner:
    """Return a concrete runner for a provider string.

    Raises ValueError for unknown provider, ImportError if the cloud SDK
    for that provider isn't installed, LocalOnlyError if
    ``global_config.local_only`` is True and ``provider`` isn't ``"ollama"``.
    """
    if provider != "ollama" and _local_only_enabled():
        raise LocalOnlyError(
            f"Local-Only is on — cloud provider {provider!r} is blocked."
        )
    if provider == "ollama":
        return LocalOllamaRunner()
    if provider == "anthropic":
        if CloudAnthropicRunner is None:
            raise ImportError(
                "anthropic provider requested but anthropic SDK is not installed. "
                "Install with: pip install anthropic"
            )
        return CloudAnthropicRunner()
    if provider in ("openai", "groq"):
        if CloudOpenAICompatRunner is None:
            raise ImportError(
                f"{provider} provider requested but openai SDK is not installed. "
                "Install with: pip install openai"
            )
        return CloudOpenAICompatRunner()
    raise ValueError(f"Unknown provider: {provider!r}")


__all__ = [
    "AgentRunner",
    "AssistantTurn",
    "LocalOnlyError",
    "ModelRef",
    "LocalOllamaRunner",
    "CloudAnthropicRunner",
    "CloudOpenAICompatRunner",
    "create_langchain_model",
    "get_runner",
]
