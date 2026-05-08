"""CloudAnthropicRunner — thin adapter over the ``anthropic`` SDK.

Anthropic needs its own runner (not OpenAI-compatible). API key sourced from
the keyring via ``secrets.get_secret('anthropic')`` with env var fallback.
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


def _map_anthropic_error(exc: BaseException) -> RunnerError:
    """Translate an Anthropic SDK / httpx exception to a RunnerError class.

    The Anthropic SDK exposes typed exceptions (anthropic.AuthenticationError,
    RateLimitError, BadRequestError, APIConnectionError) plus httpx transport
    errors. We sniff by status_code, class name, and message text.
    """
    name = type(exc).__name__
    sc = getattr(exc, "status_code", None)
    if sc is None:
        resp = getattr(exc, "response", None)
        sc = getattr(resp, "status_code", None) if resp is not None else None

    msg = str(exc)

    # Auth — explicit 401/403 or class-name match.
    if sc in (401, 403) or "AuthenticationError" in name or "PermissionDeniedError" in name:
        return RunnerAuth(f"anthropic auth error: {msg}")
    # Rate limit.
    if sc == 429 or "RateLimit" in name:
        return RunnerRateLimit(f"anthropic rate limit: {msg}")
    # Context overflow (Anthropic 400 with specific message).
    if sc == 400 and ("prompt is too long" in msg.lower() or "max_tokens" in msg.lower() and "context" in msg.lower()):
        return RunnerContextOverflow(f"anthropic context overflow: {msg}")
    # Transient (httpx / connection).
    if name in ("TransportError", "TimeoutException", "ConnectError", "APIConnectionError"):
        return RunnerTransient(f"anthropic transient: {msg}")
    return RunnerUnknown(f"anthropic unknown ({name}): {msg}")


def _current_span_safe() -> Any:
    """Return the currently-active OTel span, or None.

    Best-effort — never raises. Used to attach runner attributes when an
    enclosing pattern/orchestrator span exists, without forcing OTel as
    a hard dependency.
    """
    try:
        from opentelemetry import trace as _trace  # type: ignore

        span = _trace.get_current_span()
        # NonRecordingSpan has no useful set_attribute. Return None to skip.
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
        # OTel span attributes attached at the end (feat: c10). LangChain's
        # instrumentation doesn't cover raw SDK calls; we record provider /
        # model / latency on the active OTel span if one exists. Best-effort.
        _span = _current_span_safe()
        _start = time.perf_counter()
        if _span is not None:
            _set_attr_safe(_span, "ls.runner.provider", self.provider)
            _set_attr_safe(_span, "ls.runner.model", model_ref.get("name", ""))

        client = self._get_client()
        tool_schemas = _convert_tools(tools)

        create_kwargs: dict[str, Any] = {
            "model": model_ref["name"],
            "max_tokens": (ctx or {}).get("max_tokens", 4096),
            "messages": [{"role": "user", "content": prompt}],
        }
        # Prompt caching (Anthropic ephemeral cache, feat: c2).
        # Cache the system prompt and the tools array — these are the long-
        # lived prefixes worth amortizing across turns. The user message is
        # the variable part; never cached. Anthropic's idiom: attach
        # `cache_control` to the LAST entry in the tools array (caches all
        # preceding tool definitions) and to the system block.
        if system:
            # System becomes a list of typed blocks so we can attach cache_control.
            create_kwargs["system"] = [
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        if tool_schemas:
            tools_with_cache = list(tool_schemas)
            # Mutate a copy of the last tool dict so we don't shadow the input.
            tools_with_cache[-1] = {
                **tools_with_cache[-1],
                "cache_control": {"type": "ephemeral"},
            }
            create_kwargs["tools"] = tools_with_cache

        # Wrap the SDK call in retry/backoff (feat: c4). Transient errors
        # (httpx.TransportError, httpx.TimeoutException) and 429 retry up to
        # 3 attempts; auth/4xx fail loud immediately. Post-retry, exhausted
        # exceptions are re-raised as normalized RunnerError subclasses
        # (feat: c10) so callers don't need to know which SDK threw.
        async def _do_call() -> Any:
            return await client.messages.create(**create_kwargs)

        try:
            resp = await with_retry(_do_call)
        except RunnerError:
            raise  # already normalized
        except Exception as exc:  # noqa: BLE001
            raise _map_anthropic_error(exc) from exc

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
            # Anthropic cache fields (feat: c2). Pass through verbatim when
            # present; the SDK exposes them on the usage object only when
            # the request used cache_control blocks.
            cache_creation = getattr(um, "cache_creation_input_tokens", None)
            cache_read = getattr(um, "cache_read_input_tokens", None)
            if cache_creation is not None:
                usage["cache_creation_input_tokens"] = int(cache_creation or 0)
            if cache_read is not None:
                usage["cache_read_input_tokens"] = int(cache_read or 0)

        if _span is not None:
            _set_attr_safe(_span, "ls.runner.latency_ms", int((time.perf_counter() - _start) * 1000))
            _set_attr_safe(_span, "ls.runner.input_tokens", int(usage.get("input_tokens", 0)))
            _set_attr_safe(_span, "ls.runner.output_tokens", int(usage.get("output_tokens", 0)))

        return {
            "content": content,
            "tool_calls": tool_calls,
            "usage": usage,
            "model": model_ref["name"],
            "provider": self.provider,
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
        """Token-level streaming via ``client.messages.stream()`` (feat: c6).

        Yields ``{"delta": str, "done": False}`` for each text increment,
        then a single final ``{"delta": "", "done": True, "final": AssistantTurn}``.
        On error, yields one terminal chunk with ``done=True`` and ``error``.
        """
        client = self._get_client()
        tool_schemas = _convert_tools(tools)

        create_kwargs: dict[str, Any] = {
            "model": model_ref["name"],
            "max_tokens": (ctx or {}).get("max_tokens", 4096),
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            create_kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        if tool_schemas:
            tools_with_cache = list(tool_schemas)
            tools_with_cache[-1] = {
                **tools_with_cache[-1],
                "cache_control": {"type": "ephemeral"},
            }
            create_kwargs["tools"] = tools_with_cache

        full_text = ""
        try:
            async with client.messages.stream(**create_kwargs) as stream:
                async for delta in stream.text_stream:
                    full_text += delta
                    yield {"delta": delta, "done": False}
                final_msg = await stream.get_final_message()
        except Exception as exc:  # noqa: BLE001
            yield {"delta": "", "done": True, "error": str(exc)}
            return

        # Build final AssistantTurn from the assembled message.
        usage: Usage = {}
        um = getattr(final_msg, "usage", None)
        if um is not None:
            usage = {
                "input_tokens": int(getattr(um, "input_tokens", 0) or 0),
                "output_tokens": int(getattr(um, "output_tokens", 0) or 0),
                "total_tokens": int(
                    (getattr(um, "input_tokens", 0) or 0)
                    + (getattr(um, "output_tokens", 0) or 0)
                ),
            }
            cache_creation = getattr(um, "cache_creation_input_tokens", None)
            cache_read = getattr(um, "cache_read_input_tokens", None)
            if cache_creation is not None:
                usage["cache_creation_input_tokens"] = int(cache_creation or 0)
            if cache_read is not None:
                usage["cache_read_input_tokens"] = int(cache_read or 0)

        final_turn: AssistantTurn = {
            "content": full_text,
            "tool_calls": [],  # tool calls in streaming are surfaced via stream events; bridge in a follow-up
            "usage": usage,
            "model": model_ref["name"],
            "provider": self.provider,
            "raw": final_msg,
        }
        yield {"delta": "", "done": True, "final": final_turn}
