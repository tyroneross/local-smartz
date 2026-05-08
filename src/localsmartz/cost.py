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


# Rate cards in USD per 1M tokens (input, output). Last verified 2026-05-08
# from the provider pricing pages. ⚠️ Treat as advisory; always re-verify
# before deploying at scale.
LAST_UPDATED = date(2026, 5, 8)
# LAST_VERIFIED is the canonical name going forward (feat: c9). Kept as an
# alias for LAST_UPDATED — existing callers don't break. New code should
# read LAST_VERIFIED.
LAST_VERIFIED = LAST_UPDATED

# Days before pricing is considered stale. Crossed → pricing_freshness()
# returns stale=True; doctor probes warn; UI surfaces a banner.
PRICING_STALE_AFTER_DAYS = 30


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
    # for small workloads; these are the paid rates as of 2026-05-08.
    # Removed: mixtral-8x7b-32768, qwen-2.5-32b (deprecated in Groq's
    # production model list).
    # ⚠️ Llama-4 Maverick / Scout / Compound rates are TAG:UNVERIFIED —
    # confirm against https://console.groq.com/docs/models before relying
    # on the cost output. Setting rate_known=False is preferable to a
    # confidently-wrong number.
    "llama-3.3-70b-versatile": {"input_per_1m": 0.59, "output_per_1m": 0.79, "note": "groq"},
    "llama-3.1-8b-instant": {"input_per_1m": 0.05, "output_per_1m": 0.08, "note": "groq cheap"},
    "meta-llama/llama-4-maverick-17b-128e-instruct": {"input_per_1m": 0.20, "output_per_1m": 0.60, "note": "groq vision+128k UNVERIFIED"},
    "meta-llama/llama-4-scout-17b-16e-instruct": {"input_per_1m": 0.11, "output_per_1m": 0.34, "note": "groq UNVERIFIED"},
    "groq/compound": {"input_per_1m": 0.50, "output_per_1m": 0.85, "note": "groq compound UNVERIFIED"},
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


def pricing_freshness() -> dict:
    """Return the pricing freshness envelope (feat: c9).

    ``stale`` flips to ``True`` once the rate table is older than
    ``PRICING_STALE_AFTER_DAYS`` (default 30). Callers (UI, doctor probe)
    decide what to do with the signal — banner, warn, or block deploys.

    The provider pricing URLs for manual re-verification are documented in
    ``scripts/refresh-pricing.py``.
    """
    age = rate_age_days()
    return {
        "last_verified": LAST_VERIFIED.isoformat(),
        "age_days": age,
        "stale": age > PRICING_STALE_AFTER_DAYS,
        "stale_threshold_days": PRICING_STALE_AFTER_DAYS,
    }


def cost_from_usage(*, model: str, usage: dict, pattern: str = "single") -> dict:
    """Compute USD cost from a real ``Usage`` dict (preferred path).

    ``usage`` is the dict returned by the cloud runners (see
    ``runners.base.Usage``). Keys read: ``input_tokens``, ``output_tokens``,
    plus optional Anthropic cache fields ``cache_creation_input_tokens`` and
    ``cache_read_input_tokens`` (surfaced verbatim in the envelope; cache-
    discount math is deferred — see ``.build-loop/issues/cache-discount-math.md``).

    Returns the same envelope shape as ``estimate_cost_usd`` plus a
    ``source`` field: ``"sdk"`` when ``usage`` carries non-zero token
    counts, ``"estimate"`` when it doesn't (caller passed an empty dict).

    The deepagents memory note: this function is what every NEW caller
    should use; ``estimate_cost_usd`` is kept for the no-usage (pre-call)
    case where we only have a prompt string.
    """
    if not usage or not isinstance(usage, dict):
        # Fall back to estimate. Caller must pass prompt separately for
        # estimate; without a prompt we return zeros + source=estimate.
        envelope = estimate_cost_usd(model=model, prompt="", pattern=pattern)
        envelope["source"] = "estimate"
        return envelope

    input_tokens = int(usage.get("input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    cache_creation = int(usage.get("cache_creation_input_tokens", 0) or 0)
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)

    if input_tokens == 0 and output_tokens == 0:
        envelope = estimate_cost_usd(model=model, prompt="", pattern=pattern)
        envelope["source"] = "estimate"
        return envelope

    rate = RATES.get(model)
    if rate is None:
        return {
            "model": model,
            "pattern": pattern,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": cache_creation,
            "cache_read_input_tokens": cache_read,
            "estimated_usd": 0.0,
            "rate_known": False,
            "source": "sdk",
            "last_updated": LAST_UPDATED.isoformat(),
        }

    # NOTE: Cache discount math is deferred. Anthropic charges cache reads
    # at ~10% of the input rate and cache writes at ~125%, but we surface
    # the raw counts here and let downstream UI / billing apply the math.
    # See .build-loop/issues/cache-discount-math.md.
    cost = (
        input_tokens * rate["input_per_1m"] / 1_000_000
        + output_tokens * rate["output_per_1m"] / 1_000_000
    )
    return {
        "model": model,
        "pattern": pattern,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation,
        "cache_read_input_tokens": cache_read,
        "estimated_usd": round(cost, 4),
        "rate_known": True,
        "source": "sdk",
        "last_updated": LAST_UPDATED.isoformat(),
    }
