"""CloudAnthropicRunner — thin adapter over the ``anthropic`` SDK.

Anthropic needs its own runner (not OpenAI-compatible). API key sourced from
the keyring via ``secrets.get_secret('anthropic')`` with env var fallback.
"""
from __future__ import annotations

import os
from typing import Any

from localsmartz.runners.base import AssistantTurn, ModelRef, ToolCall, Usage


def _load_api_key() -> str | None:
    """Look up the Anthropic API key (keyring → env → None)."""
    try:
        from localsmartz import secrets

        key = secrets.get_secret("anthropic") if hasattr(secrets, "get_secret") else None
        if key:
            return key
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get("ANTHROPIC_API_KEY")


def _convert_tools(tools: list[Any] | None) -> list[dict[str, Any]]:
    """Convert LangChain tools into Anthropic tool-schema dicts.

    Best-effort: if a tool exposes ``.args_schema`` (pydantic) or ``.name`` +
    ``.description`` we lift those; otherwise skip. Anthropic's native schema
    is ``{"name", "description", "input_schema": {...}}``.
    """
    if not tools:
        return []
    out: list[dict[str, Any]] = []
    for t in tools:
        name = getattr(t, "name", None) or getattr(t, "__name__", None)
        if not isinstance(name, str):
            continue
        desc = getattr(t, "description", "") or ""
        schema_cls = getattr(t, "args_schema", None)
        input_schema: dict[str, Any] = {"type": "object", "properties": {}}
        if schema_cls is not None and hasattr(schema_cls, "model_json_schema"):
            try:
                input_schema = schema_cls.model_json_schema()
            except Exception:  # noqa: BLE001
                pass
        out.append(
            {
                "name": name,
                "description": desc,
                "input_schema": input_schema,
            }
        )
    return out


class CloudAnthropicRunner:
    provider = "anthropic"

    def __init__(self, api_key: str | None = None) -> None:
        # Defer client construction until the first call so import alone
        # doesn't fail when the SDK is installed but no key is set.
        self._api_key = api_key or _load_api_key()
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic  # type: ignore
        except ImportError as e:  # pragma: no cover
            raise ImportError(
                "anthropic SDK not installed. Install with: pip install anthropic"
            ) from e
        if not self._api_key:
            raise RuntimeError(
                "Anthropic API key not found. Set via `localsmartz secrets set anthropic=...` "
                "or ANTHROPIC_API_KEY env var."
            )
        self._client = anthropic.AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def run_turn(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        model_ref: ModelRef,
        system: str | None = None,
        ctx: dict[str, Any] | None = None,
    ) -> AssistantTurn:
        client = self._get_client()
        tool_schemas = _convert_tools(tools)

        create_kwargs: dict[str, Any] = {
            "model": model_ref["name"],
            "max_tokens": (ctx or {}).get("max_tokens", 4096),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            create_kwargs["system"] = system
        if tool_schemas:
            create_kwargs["tools"] = tool_schemas

        resp = await client.messages.create(**create_kwargs)

        content = ""
        tool_calls: list[ToolCall] = []
        for block in getattr(resp, "content", []) or []:
            btype = getattr(block, "type", None)
            if btype == "text":
                content += getattr(block, "text", "") or ""
            elif btype == "tool_use":
                tool_calls.append(
                    {
                        "name": getattr(block, "name", ""),
                        "arguments": getattr(block, "input", {}) or {},
                        "id": getattr(block, "id", ""),
                    }
                )

        um = getattr(resp, "usage", None)
        usage: Usage = {}
        if um is not None:
            usage = {
                "input_tokens": int(getattr(um, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(um, "output_tokens", 0) or 0),
                "total_tokens": int(
                    (getattr(um, "input_tokens", 0) or 0)
                    + (getattr(um, "output_tokens", 0) or 0)
                ),
            }

        return {
            "content": content,
            "tool_calls": tool_calls,
            "usage": usage,
            "model": model_ref["name"],
            "provider": self.provider,
            "raw": resp,
        }
