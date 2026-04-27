"""CloudOpenAICompatRunner — one adapter for OpenAI + Groq (same SDK, different base_url).

``model_ref["base_url"]`` overrides the default OpenAI endpoint. Groq's base URL
is ``https://api.groq.com/openai/v1``. API key sourced by provider name (``openai`` or
``groq``) from keyring → env.
"""
from __future__ import annotations

import os
from typing import Any

from localsmartz.runners.base import AssistantTurn, ModelRef, ToolCall, Usage


def _env_name_for(provider: str) -> str:
    if provider == "groq":
        return "GROQ_API_KEY"
    return "OPENAI_API_KEY"


def _load_api_key(provider: str) -> str | None:
    try:
        from localsmartz import secrets

        if hasattr(secrets, "get_secret"):
            key = secrets.get_secret(provider)
            if key:
                return key
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get(_env_name_for(provider))


def _default_base_url(provider: str) -> str | None:
    if provider == "groq":
        return "https://api.groq.com/openai/v1"
    return None  # OpenAI uses the SDK default


def _convert_tools(tools: list[Any] | None) -> list[dict[str, Any]]:
    """Convert LangChain tools into OpenAI tool-schema dicts (same shape as ChatCompletions)."""
    if not tools:
        return []
    out: list[dict[str, Any]] = []
    for t in tools:
        name = getattr(t, "name", None) or getattr(t, "__name__", None)
        if not isinstance(name, str):
            continue
        desc = getattr(t, "description", "") or ""
        schema_cls = getattr(t, "args_schema", None)
        parameters: dict[str, Any] = {"type": "object", "properties": {}}
        if schema_cls is not None and hasattr(schema_cls, "model_json_schema"):
            try:
                parameters = schema_cls.model_json_schema()
            except Exception:  # noqa: BLE001
                pass
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": parameters,
                },
            }
        )
    return out


class CloudOpenAICompatRunner:
    """Runner for both OpenAI and Groq (same API shape).

    The runner reads ``model_ref["provider"]`` at call time, so one instance
    can serve either — useful for quick A/B diffs. Base URL is resolved from
    ``model_ref["base_url"]`` if set, else provider default.
    """

    @property
    def provider(self) -> str:
        # Informational; real provider is on the model_ref.
        return "openai-compat"

    def _make_client(self, provider: str, base_url: str | None) -> Any:
        try:
            import openai  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "openai SDK not installed. Install with: pip install openai"
            ) from e
        key = _load_api_key(provider)
        if not key:
            env = _env_name_for(provider)
            raise RuntimeError(
                f"{provider} API key not found. Set via `localsmartz secrets set {provider}=...` "
                f"or {env} env var."
            )
        kwargs: dict[str, Any] = {"api_key": key}
        resolved_base = base_url or _default_base_url(provider)
        if resolved_base:
            kwargs["base_url"] = resolved_base
        return openai.AsyncOpenAI(**kwargs)

    async def run_turn(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        model_ref: ModelRef,
        system: str | None = None,
        ctx: dict[str, Any] | None = None,
    ) -> AssistantTurn:
        provider = model_ref.get("provider", "openai")
        client = self._make_client(provider, model_ref.get("base_url"))
        tool_schemas = _convert_tools(tools)

        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        create_kwargs: dict[str, Any] = {
            "model": model_ref["name"],
            "messages": messages,
            "temperature": 0,
        }
        if tool_schemas:
            create_kwargs["tools"] = tool_schemas
        mx = (ctx or {}).get("max_tokens")
        if mx is not None:
            create_kwargs["max_tokens"] = mx

        resp = await client.chat.completions.create(**create_kwargs)

        choice = resp.choices[0] if getattr(resp, "choices", None) else None
        msg = getattr(choice, "message", None) if choice is not None else None
        content = getattr(msg, "content", "") or "" if msg is not None else ""

        tool_calls: list[ToolCall] = []
        for tc in getattr(msg, "tool_calls", None) or []:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            args_raw = getattr(fn, "arguments", "") or ""
            try:
                import json

                args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw or {})
            except Exception:  # noqa: BLE001
                args = {"_raw": args_raw}
            tool_calls.append(
                {
                    "name": getattr(fn, "name", "") or "",
                    "arguments": args if isinstance(args, dict) else {"_raw": args},
                    "id": getattr(tc, "id", "") or "",
                }
            )

        um = getattr(resp, "usage", None)
        usage: Usage = {}
        if um is not None:
            usage = {
                "input_tokens": int(getattr(um, "prompt_tokens", 0) or 0),
                "output_tokens": int(getattr(um, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(um, "total_tokens", 0) or 0),
            }

        return {
            "content": content,
            "tool_calls": tool_calls,
            "usage": usage,
            "model": model_ref["name"],
            "provider": provider,
            "raw": resp,
        }
