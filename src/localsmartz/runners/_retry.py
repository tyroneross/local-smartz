"""Exponential-backoff retry helper for cloud SDK calls (feat: c4).

Why this is hand-rolled instead of LangChain's ``with_retry``:

    See ``~/.claude/projects/-Users-tyroneross/memory/reference_deepagents_runnable_retry.md``.
    LangChain's ``RunnableRetry`` is unhashable inside DeepAgents' model
    cache and strips ``bind_tools``. Cloud runners go through both DeepAgents
    (Path A) and direct ``run_turn`` (Path B); retry must live at the SDK
    call site, NOT at the chat-model wrapper layer.

What we retry:
    - ``httpx.TransportError`` (connection, DNS, TLS)
    - ``httpx.TimeoutException``
    - HTTP 429 (rate limit; honors ``Retry-After`` header when present)

What we DO NOT retry:
    - 4xx auth (401, 403)
    - 400 context overflow / invalid request
    - 5xx other than 429 (these propagate; SDK semantics decide)
    - Anything else — fail loud, no silent retry
"""
from __future__ import annotations

import asyncio
import random
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_BASE_DELAY = 1.0
_DEFAULT_MAX_DELAY = 20.0


def _is_transient(exc: BaseException) -> bool:
    """Classify an exception as transient (retryable) or not.

    Imports httpx lazily — runners that don't use httpx (none today) still
    work. Falls back to a name-based check for environments where the
    httpx import isn't visible at our scope.
    """
    name = type(exc).__name__
    if name in ("TransportError", "TimeoutException", "ConnectError", "ReadTimeout", "WriteTimeout", "PoolTimeout"):
        return True
    try:
        import httpx  # type: ignore

        if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _is_rate_limit(exc: BaseException) -> bool:
    """Detect a 429 from the various SDK shapes.

    Anthropic + OpenAI SDKs raise typed exceptions with a ``status_code``
    or ``response`` attribute. We sniff for the 429 marker.
    """
    # Direct status_code attribute (anthropic/openai both expose).
    sc = getattr(exc, "status_code", None)
    if sc == 429:
        return True
    resp = getattr(exc, "response", None)
    if resp is not None:
        rsc = getattr(resp, "status_code", None)
        if rsc == 429:
            return True
    # Class-name fallback for SDK-specific subclasses.
    name = type(exc).__name__
    if "RateLimit" in name:
        return True
    return False


def _retry_after_seconds(exc: BaseException) -> float | None:
    """Extract Retry-After seconds from a 429 response, if present."""
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    headers = getattr(resp, "headers", None) or {}
    val = None
    if isinstance(headers, dict):
        val = headers.get("retry-after") or headers.get("Retry-After")
    else:
        try:
            val = headers.get("retry-after") or headers.get("Retry-After")
        except Exception:  # noqa: BLE001
            return None
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
    base_delay: float = _DEFAULT_BASE_DELAY,
    max_delay: float = _DEFAULT_MAX_DELAY,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Call ``fn()`` with exponential-backoff retry on transient failures.

    ``fn`` is a zero-arg async callable returning T. Auth/4xx other than
    429 raise immediately on the first attempt; transient errors and 429
    retry up to ``max_attempts`` total, with delay = min(max_delay,
    base_delay * 2**(attempt-1)) plus a small jitter.

    429 honors ``Retry-After`` header when present, capped at max_delay.

    The ``sleep`` argument is injectable for tests.
    """
    attempt = 0
    last_exc: BaseException | None = None
    while attempt < max_attempts:
        attempt += 1
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            transient = _is_transient(exc)
            rate_limited = _is_rate_limit(exc)
            if not (transient or rate_limited):
                # Auth, context-overflow, 4xx — fail loud, no retry.
                raise
            if attempt >= max_attempts:
                raise
            # Compute delay.
            if rate_limited:
                ra = _retry_after_seconds(exc)
                delay = min(max_delay, ra if ra is not None else base_delay * (2 ** (attempt - 1)))
            else:
                delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            # Small jitter to spread retries across concurrent callers.
            delay = delay + random.uniform(0, 0.25 * delay)
            delay = min(delay, max_delay)
            await sleep(delay)
    # Safety: shouldn't reach here, but if max_attempts <= 0 was passed.
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("with_retry exhausted without exception (max_attempts <= 0?)")


__all__ = ["with_retry"]
