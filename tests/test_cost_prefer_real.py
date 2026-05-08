"""Tests for cost.cost_from_usage (feat: c3) — prefer SDK token counts."""
from __future__ import annotations

import pytest

from localsmartz import cost


def test_cost_from_usage_with_real_tokens_known_rate():
    out = cost.cost_from_usage(
        model="claude-sonnet-4-6",
        usage={"input_tokens": 1_000_000, "output_tokens": 500_000},
    )
    assert out["source"] == "sdk"
    assert out["rate_known"] is True
    assert out["input_tokens"] == 1_000_000
    assert out["output_tokens"] == 500_000
    # claude-sonnet-4-6: $3/1M input + $15/1M output → 3.0 + 7.5 = $10.50
    assert out["estimated_usd"] == 10.5
    assert out["cache_creation_input_tokens"] == 0
    assert out["cache_read_input_tokens"] == 0


def test_cost_from_usage_with_cache_fields():
    out = cost.cost_from_usage(
        model="claude-haiku-4",
        usage={
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_creation_input_tokens": 800,
            "cache_read_input_tokens": 200,
        },
    )
    # Cache fields surfaced verbatim; not yet applied to billing math (deferred).
    assert out["cache_creation_input_tokens"] == 800
    assert out["cache_read_input_tokens"] == 200
    assert out["source"] == "sdk"


def test_cost_from_usage_unknown_model_returns_zero():
    out = cost.cost_from_usage(
        model="some-future-model",
        usage={"input_tokens": 100, "output_tokens": 50},
    )
    assert out["rate_known"] is False
    assert out["estimated_usd"] == 0.0
    assert out["source"] == "sdk"
    assert out["input_tokens"] == 100


def test_cost_from_usage_empty_usage_falls_back_to_estimate():
    out = cost.cost_from_usage(
        model="claude-haiku-4",
        usage={},
    )
    assert out["source"] == "estimate"


def test_cost_from_usage_zero_tokens_falls_back_to_estimate():
    """When SDK reports both input=0 and output=0, treat as estimate (likely a no-op call)."""
    out = cost.cost_from_usage(
        model="claude-haiku-4",
        usage={"input_tokens": 0, "output_tokens": 0},
    )
    assert out["source"] == "estimate"


def test_cost_from_usage_none_usage_falls_back_to_estimate():
    out = cost.cost_from_usage(model="claude-haiku-4", usage=None)  # type: ignore[arg-type]
    assert out["source"] == "estimate"


def test_estimate_cost_usd_unchanged():
    """c3 must not alter estimate_cost_usd semantics — regression guard."""
    out = cost.estimate_cost_usd(
        model="claude-sonnet-4-6",
        prompt="hello world " * 100,  # ~1100 chars → ~275 input tokens
        pattern="single",
    )
    assert "source" not in out  # estimate_cost_usd does NOT add source
    assert out["rate_known"] is True
    assert out["model"] == "claude-sonnet-4-6"


def test_cost_from_usage_pattern_passthrough_on_estimate_fallback():
    """When falling back to estimate, the pattern arg must propagate."""
    out = cost.cost_from_usage(
        model="claude-haiku-4",
        usage={},
        pattern="critic_loop",
    )
    assert out["pattern"] == "critic_loop"
