"""CloudOpenAICompatRunner — one adapter for OpenAI + Groq (same SDK, different base_url).

``model_ref["base_url"]`` overrides the default OpenAI endpoint. Groq's base URL
is ``https://api.groq.com/openai/v1``. API key sourced by provider name (``openai`` or
``groq``) from keyring → env.
"""
from __future__ import annotations

import os
import time
from typing import Any

from typing import AsyncIterator

from localsmartz.runners._retry import with_retry
from localsmartz.runners.base import (
    AssistantTurn,
    ModelRef,
    RunnerAuth,
    RunnerContextOverflow,
    RunnerError,
    RunnerRateLimit,
    RunnerTransient,
    RunnerUnknown,
    StreamChunk,
    ToolCall,
    Usage,
)


def _map_openai_error(exc: BaseException) -> RunnerError:
    """Translate an OpenAI/Groq SDK / httpx exception to a RunnerError.

    Both OpenAI and Groq use the openai Python SDK; same exception shapes.
    """
    name = type(exc).__name__
    sc = getattr(exc, "status_code", None)
    if sc is None:
        resp = getattr(exc, "response", None)
        sc = getattr(resp, "status_code", None) if resp is not None else None

    msg = str(exc)

    if sc in (401, 403) or "AuthenticationError" in name or "PermissionDeniedError" in name:
        return RunnerAuth(f"openai-compat auth error: {msg}")
    if sc == 429 or "RateLimit" in name:
        return RunnerRateLimit(f"openai-compat rate limit: {msg}")
    # Context overflow surfaces as 400 with code 'context_length_exceeded'
    # or message text.
    code = getattr(exc, "code", None)
    if code == "context_length_exceeded" or (sc == 400 and "context_length" in msg.lower()):
        return RunnerContextOverflow(f"openai-compat context overflow: {msg}")
    if name in ("TransportError", "TimeoutException", "ConnectError", "APIConnectionError"):
        return RunnerTransient(f"openai-compat transient: {msg}")
    return RunnerUnknown(f"openai-compat unknown ({name}): {msg}")


def _current_span_safe() -> Any:
    """Return the currently-active OTel span, or None. Best-effort."""
    try:
        from opentelemetry import trace as _trace  # type: ignore

        span = _trace.get_current_span()
        if hasattr(span, "is_recording") and not span.is_recording():
            return None
        return span
    except Exception:  # noqa: BLE001
        return None


def _set_attr_safe(span: Any, key: str, value: Any) -> None:
    try:
        span.set_attribute(key, value)
    except Exception:  # noqa: BLE001
        pass


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
        # OTel attributes attached at end (feat: c10). LangChain instrumentation
        # doesn't cover raw SDK calls. See cloud_anthropic.py for the rationale.
        _span = _current_span_safe()
        _start = time.perf_counter()
        provider = model_ref.get("provider", "openai")
        if _span is not None:
            _set_attr_safe(_span, "ls.runner.provider", provider)
            _set_attr_safe(_span, "ls.runner.model", model_ref.get("name", ""))
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

        # Wrap the SDK call in retry/backoff (feat: c4). Same policy as
        # the Anthropic runner. Post-retry, exhausted exceptions are
        # re-raised as normalized RunnerError subclasses (feat: c10).
        async def _do_call() -> Any:
            return await client.chat.completions.create(**create_kwargs)

        try:
            resp = await with_retry(_do_call)
        except RunnerError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _map_openai_error(exc) from exc

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

        if _span is not None:
            _set_attr_safe(_span, "ls.runner.latency_ms", int((time.perf_counter() - _start) * 1000))
            _set_attr_safe(_span, "ls.runner.input_tokens", int(usage.get("input_tokens", 0)))
            _set_attr_safe(_span, "ls.runner.output_tokens", int(usage.get("output_tokens", 0)))

        return {
            "content": content,
            "tool_calls": tool_calls,
            "usage": usage,
            "model": model_ref["name"],
            "provider": provider,
            "raw": resp,
        }

    async def stream_turn(
        self,
        prompt: str,
        *,
        tools: list[Any] | None = None,
        model_ref: ModelRef,
        system: str | None = None,
        ctx: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Token-level streaming via ``stream=True`` (feat: c6).

        Works for both OpenAI and Groq (same OpenAI-compatible API). Yields
        ``{"delta": str, "done": False}`` per content chunk, then a single
        terminal ``{"delta": "", "done": True, "final": AssistantTurn}``.
        On error: ``{"delta": "", "done": True, "error": str}``.
        """
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
            "stream": True,
            # Some providers (Groq, OpenAI) require this to surface usage on stream end.
            "stream_options": {"include_usage": True},
        }
        if tool_schemas:
            create_kwargs["tools"] = tool_schemas
        mx = (ctx or {}).get("max_tokens")
        if mx is not None:
            create_kwargs["max_tokens"] = mx

        full_text = ""
        usage: Usage = {}
        try:
            stream = await client.chat.completions.create(**create_kwargs)
            async for chunk in stream:
                # OpenAI chunk shape: chunk.choices[0].delta.content (None or str).
                choices = getattr(chunk, "choices", None) or []
                if choices:
                    delta_msg = getattr(choices[0], "delta", None)
                    if delta_msg is not None:
                        text = getattr(delta_msg, "content", None)
                        if text:
                            full_text += text
                            yield {"delta": text, "done": False}
                # Final usage chunk (when stream_options.include_usage=True,
                # the SDK delivers a chunk with usage populated and empty choices).
                um = getattr(chunk, "usage", None)
                if um is not None:
                    usage = {
                        "input_tokens": int(getattr(um, "prompt_tokens", 0) or 0),
                        "output_tokens": int(getattr(um, "completion_tokens", 0) or 0),
                        "total_tokens": int(getattr(um, "total_tokens", 0) or 0),
                    }
        except Exception as exc:  # noqa: BLE001
            yield {"delta": "", "done": True, "error": str(exc)}
            return

        final_turn: AssistantTurn = {
            "content": full_text,
            "tool_calls": [],
            "usage": usage,
            "model": model_ref["name"],
            "provider": provider,
            "raw": None,
        }
        yield {"delta": "", "done": True, "final": final_turn}
