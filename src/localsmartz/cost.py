"""Per-provider rate table + token estimator.

Every cloud run surfaces an estimated cost to the user before execution
(see research doc §F11). No threshold — explicit confirm per spec decision
#4 (2026-04-23). Rates are hand-curated; doctor warns when stale.

The estimator is intentionally crude — prompt chars → tokens ≈ chars*0.25,
output tokens assumed equal to prompt tokens × 2 (typical research-loop
shape). Good enough for a "will this cost $0.05 or $5.00?" signal.
"""
from __future__ import annotations

from datetime import date
from typing import TypedDict


# Rate cards in USD per 1M tokens (input, output). Last verified 2026-04-23
# from the provider pricing pages. ⚠️ Treat as advisory; always re-verify
# before deploying at scale.
LAST_UPDATED = date(2026, 4, 23)


class Rate(TypedDict):
    input_per_1m: float
    output_per_1m: float
    note: str


RATES: dict[str, Rate] = {
    # Anthropic
    "claude-opus-4-7": {"input_per_1m": 15.0, "output_per_1m": 75.0, "note": "frontier"},
    "claude-sonnet-4-6": {"input_per_1m": 3.0, "output_per_1m": 15.0, "note": "default cloud"},
    "claude-haiku-4": {"input_per_1m": 0.80, "output_per_1m": 4.0, "note": "cheap"},
    # OpenAI
    "gpt-4o": {"input_per_1m": 2.50, "output_per_1m": 10.0, "note": ""},
    "gpt-4o-mini": {"input_per_1m": 0.15, "output_per_1m": 0.60, "note": "cheap"},
    # Groq — OpenAI-compatible endpoint, their own models. Free tier exists
    # for small workloads; these are the paid rates as of 2026-04-23.
    "llama-3.3-70b-versatile": {"input_per_1m": 0.59, "output_per_1m": 0.79, "note": "groq"},
    "mixtral-8x7b-32768": {"input_per_1m": 0.24, "output_per_1m": 0.24, "note": "groq"},
    "qwen-2.5-32b": {"input_per_1m": 0.79, "output_per_1m": 0.79, "note": "groq"},
}


def estimate_tokens(text: str) -> int:
    """Crude chars→tokens estimator. 4 chars ≈ 1 token for English."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def estimate_cost_usd(
    *,
    model: str,
    prompt: str,
    pattern: str = "single",
    max_iterations: int = 1,
) -> dict:
    """Estimate total USD cost for a planned run.

    Returns ``{"estimated_usd", "input_tokens", "output_tokens",
    "model", "pattern", "rate_known"}``. If the model isn't in ``RATES``,
    returns ``rate_known: False`` and zero estimate — UI shows "Rate unknown"
    and lets the user continue at their own risk.
    """
    input_tokens = estimate_tokens(prompt)
    # Patterns with more turns amplify cost.
    multiplier = {
        "single": 1,
        "chain": 3,       # 3 stages
        "router": 2,      # router + specialist
        "critic_loop": max_iterations * 2,  # writer + critic per iteration
    }.get(pattern, 1)
    output_tokens = input_tokens * 2 * multiplier

    rate = RATES.get(model)
    if rate is None:
        return {
            "model": model,
            "pattern": pattern,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "estimated_usd": 0.0,
            "rate_known": False,
            "last_updated": LAST_UPDATED.isoformat(),
        }
    cost = (
        input_tokens * rate["input_per_1m"] / 1_000_000
        + output_tokens * rate["output_per_1m"] / 1_000_000
    )
    return {
        "model": model,
        "pattern": pattern,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_usd": round(cost, 4),
        "rate_known": True,
        "last_updated": LAST_UPDATED.isoformat(),
    }


def rate_age_days() -> int:
    """Days since the last rate-table update. Doctor probe warns if > 90."""
    return (date.today() - LAST_UPDATED).days
