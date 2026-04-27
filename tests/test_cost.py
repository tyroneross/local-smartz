"""Cost estimator tests."""
from __future__ import annotations

from localsmartz.cost import (
    RATES,
    estimate_cost_usd,
    estimate_tokens,
    rate_age_days,
)


def test_estimate_tokens_linear() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcd" * 10) == 10


def test_estimate_cost_known_model() -> None:
    est = estimate_cost_usd(
        model="claude-sonnet-4-6",
        prompt="x" * 4000,  # ~1000 tokens in
        pattern="single",
    )
    assert est["rate_known"] is True
    assert est["input_tokens"] == 1000
    assert est["output_tokens"] == 2000
    # 1000 * 3/1M + 2000 * 15/1M = 0.003 + 0.030 = 0.033
    assert abs(est["estimated_usd"] - 0.033) < 1e-4


def test_estimate_cost_unknown_model_returns_rate_unknown() -> None:
    est = estimate_cost_usd(model="definitely-fake-model", prompt="hi", pattern="single")
    assert est["rate_known"] is False
    assert est["estimated_usd"] == 0.0


def test_critic_loop_multiplier_amplifies_output() -> None:
    single = estimate_cost_usd(model="gpt-4o-mini", prompt="x" * 400, pattern="single")
    critic = estimate_cost_usd(
        model="gpt-4o-mini",
        prompt="x" * 400,
        pattern="critic_loop",
        max_iterations=3,
    )
    # Critic pattern with 3 iterations does 6 model passes (3 writer + 3 critic).
    assert critic["output_tokens"] == single["output_tokens"] * 6
    assert critic["estimated_usd"] > single["estimated_usd"]


def test_rate_age_days_nonnegative() -> None:
    assert rate_age_days() >= 0


def test_groq_models_priced_in_table() -> None:
    assert "llama-3.3-70b-versatile" in RATES
    assert RATES["llama-3.3-70b-versatile"]["input_per_1m"] > 0
