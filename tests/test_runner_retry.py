"""Retry/backoff tests (feat: c4) for runners._retry.with_retry.

Mocks SDK calls; never hits network. Verifies:
- Transient errors retry up to max_attempts then propagate
- Auth/4xx fail immediately (no retry)
- 429 honors Retry-After header
- Cloud runners actually go through with_retry
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from localsmartz.runners import _retry
from localsmartz.runners._retry import (
    _is_rate_limit,
    _is_transient,
    _retry_after_seconds,
    with_retry,
)
from localsmartz.runners.cloud_anthropic import CloudAnthropicRunner


def _run(coro):
    return asyncio.run(coro)


# --- classification helpers ----------------------------------------------


def test_is_transient_httpx_timeout():
    assert _is_transient(httpx.TimeoutException("boom")) is True


def test_is_transient_httpx_transport():
    assert _is_transient(httpx.ConnectError("refused")) is True


def test_is_transient_value_error_no():
    assert _is_transient(ValueError("nope")) is False


def test_is_rate_limit_status_code():
    fake = SimpleNamespace(status_code=429)
    assert _is_rate_limit(fake) is True


def test_is_rate_limit_via_response():
    fake = SimpleNamespace(response=SimpleNamespace(status_code=429))
    assert _is_rate_limit(fake) is True


def test_is_rate_limit_class_name():
    class RateLimitError(Exception):
        pass

    assert _is_rate_limit(RateLimitError("slow down")) is True


def test_is_rate_limit_400_no():
    fake = SimpleNamespace(status_code=400)
    assert _is_rate_limit(fake) is False


def test_retry_after_seconds_present():
    fake = SimpleNamespace(response=SimpleNamespace(headers={"retry-after": "7"}))
    assert _retry_after_seconds(fake) == 7.0


def test_retry_after_seconds_absent():
    fake = SimpleNamespace(response=SimpleNamespace(headers={}))
    assert _retry_after_seconds(fake) is None


# --- with_retry ----------------------------------------------------------


def test_with_retry_succeeds_first_try():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        return "ok"

    out = _run(with_retry(fn))
    assert out == "ok"
    assert calls["n"] == 1


def test_with_retry_transient_then_success():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.TimeoutException("transient")
        return "ok"

    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    out = _run(with_retry(fn, sleep=fake_sleep))
    assert out == "ok"
    assert calls["n"] == 3
    assert len(sleeps) == 2  # retried twice before succeeding


def test_with_retry_auth_no_retry():
    """4xx auth = fail loud immediately, no retry."""
    calls = {"n": 0}

    class AuthError(Exception):
        status_code = 401

    async def fn():
        calls["n"] += 1
        raise AuthError("unauthorized")

    async def fake_sleep(s: float) -> None:
        pytest.fail("sleep should not be called for auth errors")

    with pytest.raises(AuthError):
        _run(with_retry(fn, sleep=fake_sleep))
    assert calls["n"] == 1


def test_with_retry_429_honors_retry_after():
    calls = {"n": 0}

    class RateLimitError(Exception):
        def __init__(self):
            super().__init__("rate limited")
            self.status_code = 429
            self.response = SimpleNamespace(status_code=429, headers={"retry-after": "2"})

    async def fn():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RateLimitError()
        return "ok"

    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    out = _run(with_retry(fn, sleep=fake_sleep, max_delay=20.0))
    assert out == "ok"
    # Delay should be at least the Retry-After value (2.0). Jitter adds up
    # to 25% — the floor is 2.0, ceiling is 2.5.
    assert 2.0 <= sleeps[0] <= 2.5


def test_with_retry_exhausts_max_attempts():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        raise httpx.TimeoutException("always")

    async def fake_sleep(s: float) -> None:
        pass

    with pytest.raises(httpx.TimeoutException):
        _run(with_retry(fn, max_attempts=3, sleep=fake_sleep))
    assert calls["n"] == 3


def test_with_retry_max_delay_clamp():
    """Even with high attempt counts, delay is clamped at max_delay."""
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] < 4:
            raise httpx.TimeoutException("t")
        return "ok"

    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)

    out = _run(with_retry(fn, max_attempts=4, base_delay=10.0, max_delay=5.0, sleep=fake_sleep))
    assert out == "ok"
    for s in sleeps:
        assert s <= 5.0


# --- integration: runners go through with_retry --------------------------


def test_anthropic_runner_retries_on_transient():
    """End-to-end: CloudAnthropicRunner.run_turn retries when SDK raises transient."""
    runner = CloudAnthropicRunner(api_key="sk-test")
    text_block = SimpleNamespace(type="text", text="hello")
    resp = SimpleNamespace(content=[text_block], usage=SimpleNamespace(input_tokens=1, output_tokens=1))

    call_count = {"n": 0}

    async def flaky_create(**kwargs: Any) -> Any:
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise httpx.TimeoutException("transient")
        return resp

    mock_messages = MagicMock()
    mock_messages.create = AsyncMock(side_effect=flaky_create)
    runner._client = MagicMock(messages=mock_messages)

    # Patch sleep at the _retry module to avoid real waits.
    import localsmartz.runners._retry as retry_mod

    real_sleep = retry_mod.asyncio.sleep
    retry_mod.asyncio.sleep = AsyncMock(return_value=None)
    try:
        turn = _run(
            runner.run_turn(
                "hi",
                model_ref={"provider": "anthropic", "name": "claude-haiku-4"},
            )
        )
    finally:
        retry_mod.asyncio.sleep = real_sleep

    assert turn["content"] == "hello"
    assert call_count["n"] == 2  # one retry happened
