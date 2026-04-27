"""LocalOllamaRunner — wraps ChatOllama / DeepAgents stack.

Does NOT wrap in ``.with_retry()`` — see ``reference_deepagents_runnable_retry.md``.
Reads the models registry to honor ``reasoning_mode == "off-by-default"``
(qwen3.5 family mangles tool calls when reasoning is on — F22).
"""
from __future__ import annotations

from typing import Any

import httpx

from localsmartz.runners.base import AssistantTurn, ModelRef, ToolCall, Usage


def _should_disable_reasoning(model_name: str) -> bool:
    """Return True when the registry flags this model as reasoning-off-by-default.

    Best-effort: if the registry import fails we err on False (no injection).
    """
    try:
        from localsmartz.models.registry import get_model_rec

        rec = get_model_rec(model_name)
        return bool(rec and rec.get("reasoning_mode") == "off-by-default")
    except Exception:  # noqa: BLE001 — runners must not crash on registry drift
        return False


def _build_chat_ollama(model_ref: ModelRef) -> Any:
    """Construct a bare ``ChatOllama`` instance (no with_retry — see module docstring)."""
    from langchain_ollama import ChatOllama  # lazy import: heavy

    model_kwargs: dict[str, Any] = {}
    if _should_disable_reasoning(model_ref["name"]):
        # ChatOllama forwards model_kwargs into the generate payload.
        # Ollama accepts ``reasoning`` as a boolean per the qwen3.5 spec.
        model_kwargs["reasoning"] = False

    return ChatOllama(
        model=model_ref["name"],
        temperature=0,
        num_ctx=4096,
        model_kwargs=model_kwargs or None,
        client_kwargs={
            "timeout": httpx.Timeout(
                connect=5.0, read=600.0, write=30.0, pool=5.0
            ),
        },
    )


class LocalOllamaRunner:
    """Provider-agnostic wrapper around a LangChain ``ChatOllama`` instance.

    One runner per process is fine — ``ChatOllama`` instances are cheap to
    construct, and we rebuild per ``run_turn`` so model ref changes are
    honored without stale caches.
    """

    provider = "ollama"

    async def run_turn(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        model_ref: ModelRef,
        system: str | None = None,
        ctx: dict[str, Any] | None = None,
    ) -> AssistantTurn:
        llm = _build_chat_ollama(model_ref)
        if tools:
            llm = llm.bind_tools(tools)

        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        # ainvoke returns a single AIMessage; ChatOllama handles the HTTP in a
        # threadpool so we don't block the event loop.
        resp = await llm.ainvoke(messages)

        content = getattr(resp, "content", "") or ""
        if isinstance(content, list):
            # Some adapters return list of {"text": ...} segments.
            content = "".join(
                seg.get("text", "") if isinstance(seg, dict) else str(seg)
                for seg in content
            )

        tool_calls: list[ToolCall] = []
        for tc in getattr(resp, "tool_calls", []) or []:
            tool_calls.append(
                {
                    "name": tc.get("name", ""),
                    "arguments": tc.get("args", {}) or {},
                    "id": tc.get("id", ""),
                }
            )

        usage: Usage = {}
        um = getattr(resp, "usage_metadata", None)
        if isinstance(um, dict):
            usage = {
                "input_tokens": int(um.get("input_tokens", 0) or 0),
                "output_tokens": int(um.get("output_tokens", 0) or 0),
                "total_tokens": int(um.get("total_tokens", 0) or 0),
            }

        return {
            "content": content if isinstance(content, str) else "",
            "tool_calls": tool_calls,
            "usage": usage,
            "model": model_ref["name"],
            "provider": self.provider,
            "raw": resp,
        }
