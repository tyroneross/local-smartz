"""Anthropic cache-discount math tests (commit E, 2026-05-08).

Closes the c3 deferral previously tracked at
``.build-loop/issues/cache-discount-math.md``. Verifies that
``cost.cost_from_usage`` applies:

  - cache_read_input_tokens billed at  10% of the base input rate
  - cache_creation_input_tokens billed at 125% of the base input rate
  - effective_input_tokens (full price) = input_tokens - cache_creation - cache_read

The numeric assertions use ``claude-sonnet-4-6`` ($3/1M input, $15/1M
output) — round-number rates make the arithmetic obvious in the assertions.
"""
from __future__ import annotations

import pytest

from localsmartz import cost
from localsmartz.cost import (
    CACHE_READ_MULTIPLIER,
    CACHE_WRITE_MULTIPLIER,
    cost_from_usage,
)


def test_cache_multipliers_set():
    """Sanity check on the constants — refresh-pricing.py reads these."""
    assert CACHE_WRITE_MULTIPLIER == 1.25
    assert CACHE_READ_MULTIPLIER == 0.10


def test_cache_read_at_10_percent_of_input_rate():
    """1000 cache_read tokens cost 10% of what 1000 normal input tokens cost."""
    # Sonnet input rate is $3/1M → 1000 normal input tokens = $0.003
    # Cache reads should be 0.10 × that = $0.0003
    out = cost_from_usage(
        model="claude-sonnet-4-6",
        usage={
            "input_tokens": 1000,           # all of these are cache reads
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 1000,
        },
    )
    assert out["effective_input_tokens"] == 0
    # 1000 × $3/1M × 0.10 = $0.0003
    assert out["estimated_usd"] == pytest.approx(0.0003, abs=1e-6)


def test_cache_write_at_125_percent_of_input_rate():
    """1000 cache_creation tokens cost 125% of what 1000 normal input tokens cost.

    Returned estimated_usd is rounded to 4 decimals (round-half-even),
    so we compare against the rounded expected value.
    """
    out = cost_from_usage(
        model="claude-sonnet-4-6",
        usage={
            "input_tokens": 1000,
            "output_tokens": 0,
            "cache_creation_input_tokens": 1000,
            "cache_read_input_tokens": 0,
        },
    )
    assert out["effective_input_tokens"] == 0
    # 1000 × $3/1M × 1.25 = $0.00375 → rounds half-even to $0.0037 at 4dp.
    assert out["estimated_usd"] == round(0.00375, 4)


def test_mixed_input_with_cache_buckets():
    """Mixed: 200 full-price + 800 cache_creation + 1000 cache_read."""
    out = cost_from_usage(
        model="claude-sonnet-4-6",
        usage={
            "input_tokens": 2000,            # aggregate, all three buckets together
            "output_tokens": 500,
            "cache_creation_input_tokens": 800,
            "cache_read_input_tokens": 1000,
        },
    )
    assert out["effective_input_tokens"] == 200  # 2000 - 800 - 1000
    # 200 × $3/1M               = 0.0006
    # 800 × $3/1M × 1.25        = 0.003
    # 1000 × $3/1M × 0.10       = 0.0003
    # 500 × $15/1M              = 0.0075
    # total ≈ 0.0114
    expected = 0.0006 + 0.003 + 0.0003 + 0.0075
    assert out["estimated_usd"] == pytest.approx(round(expected, 4), abs=1e-6)


def test_cache_read_is_cheaper_than_no_cache():
    """Regression guard: with cache_read, total cost MUST be lower than
    if those same tokens were billed at full price.
    """
    no_cache = cost_from_usage(
        model="claude-sonnet-4-6",
        usage={"input_tokens": 1000, "output_tokens": 0},
    )
    with_cache = cost_from_usage(
        model="claude-sonnet-4-6",
        usage={
            "input_tokens": 1000,
            "output_tokens": 0,
            "cache_read_input_tokens": 1000,
        },
    )
    assert with_cache["estimated_usd"] < no_cache["estimated_usd"]
    # Should be exactly 10% of the no-cache cost.
    assert with_cache["estimated_usd"] == pytest.approx(
        no_cache["estimated_usd"] * 0.10, abs=1e-6
    )


def test_cache_write_is_more_expensive_than_no_cache():
    """Regression guard: cache writes are billed at 125%, so cost MUST
    be higher when the same tokens are cache_creation vs full-price."""
    # Use larger token counts so 4-decimal rounding doesn't squash the
    # 25% delta into rounding noise.
    no_cache = cost_from_usage(
        model="claude-sonnet-4-6",
        usage={"input_tokens": 100_000, "output_tokens": 0},
    )
    with_write = cost_from_usage(
        model="claude-sonnet-4-6",
        usage={
            "input_tokens": 100_000,
            "output_tokens": 0,
            "cache_creation_input_tokens": 100_000,
        },
    )
    assert with_write["estimated_usd"] > no_cache["estimated_usd"]
    # 100k × $3/1M = $0.30 → 1.25× = $0.375
    assert with_write["estimated_usd"] == pytest.approx(
        no_cache["estimated_usd"] * 1.25, abs=1e-4
    )


def test_effective_input_tokens_floors_at_zero():
    """Defensive: malformed payload where cache buckets > input_tokens
    must NOT produce a negative effective_input or a negative cost."""
    out = cost_from_usage(
        model="claude-sonnet-4-6",
        usage={
            "input_tokens": 100,
            "output_tokens": 0,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 200,
        },
    )
    assert out["effective_input_tokens"] == 0
    assert out["estimated_usd"] >= 0


def test_existing_envelope_keys_preserved():
    """Backward-compat: existing callers that read input_tokens etc.
    must still see the raw aggregate count, not the effective subset."""
    out = cost_from_usage(
        model="claude-haiku-4",
        usage={
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_creation_input_tokens": 300,
            "cache_read_input_tokens": 500,
        },
    )
    assert out["input_tokens"] == 1000  # unchanged from before
    assert out["output_tokens"] == 200
    assert out["cache_creation_input_tokens"] == 300
    assert out["cache_read_input_tokens"] == 500
    assert "effective_input_tokens" in out
    assert out["effective_input_tokens"] == 200  # 1000 - 300 - 500


def test_unknown_model_still_returns_effective_input_tokens():
    """Even when rate_known=False, the envelope should still surface
    effective_input_tokens — UI may render it for clarity."""
    out = cost_from_usage(
        model="some-future-model",
        usage={
            "input_tokens": 1000,
            "output_tokens": 100,
            "cache_read_input_tokens": 700,
        },
    )
    assert out["rate_known"] is False
    assert out["estimated_usd"] == 0.0
    assert out["effective_input_tokens"] == 300  # 1000 - 700
