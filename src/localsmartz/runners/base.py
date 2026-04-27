"""AgentRunner protocol + data shapes.

A ``Runner`` is a provider-agnostic interface for one agent turn: take a
prompt (+ tools + model_ref + context), return an AssistantTurn with the
text content, any tool calls, and token usage.

Patterns call ``run_turn`` repeatedly; they never touch a provider SDK
directly. This keeps the same pattern code working on local (Ollama) and
cloud (Anthropic, OpenAI, Groq).
"""
from __future__ import annotations

from typing import Any, Protocol, TypedDict


class ModelRef(TypedDict, total=False):
    """Tagged reference to a model on some provider.

    ``provider`` and ``name`` are required; ``base_url`` is only used by
    OpenAI-compatible providers (OpenAI itself, Groq).
    """
    provider: str  # "ollama" | "anthropic" | "openai" | "groq"
    name: str
    base_url: str


class ToolCall(TypedDict, total=False):
    name: str
    arguments: dict[str, Any]
    id: str


class Usage(TypedDict, total=False):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class AssistantTurn(TypedDict, total=False):
    """One model response, provider-normalized.

    ``content`` is the plain-text reply (may be empty when only tool calls
    were produced). ``tool_calls`` is a list of structured calls. ``usage``
    carries token counts when the provider reports them. ``raw`` is the
    unmodified provider response for debugging / trace export.
    """
    content: str
    tool_calls: list[ToolCall]
    usage: Usage
    model: str
    provider: str
    raw: Any


class AgentRunner(Protocol):
    """Provider-agnostic single-turn interface.

    Implementations MUST:
    - Never wrap the chat model in ``.with_retry()`` before handing to
      ``create_deep_agent`` — RunnableRetry is unhashable and strips
      ``bind_tools`` (see project memory ``reference_deepagents_runnable_retry.md``).
    - Auto-inject ``reasoning: false`` when the model registry flags
      ``reasoning_mode == "off-by-default"`` (F22 guard — qwen3.5 family
      mangles tool calls when reasoning is on).
    """

    async def run_turn(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        model_ref: ModelRef,
        system: str | None = None,
        ctx: dict[str, Any] | None = None,
    ) -> AssistantTurn:
        ...
