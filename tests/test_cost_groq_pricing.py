"""Groq model lineup tests (refresh 2026-05-08).

Verifies that the cost.RATES table:
  - Drops the deprecated ``mixtral-8x7b-32768`` and ``qwen-2.5-32b`` entries.
  - Adds ``llama-3.1-8b-instant`` (cheap tier).
  - Adds the Llama-4 Maverick / Scout entries (mid/strong tier picks).
  - Adds ``groq/compound``.

Pricing values are tagged UNVERIFIED in cost.py for the Llama-4 family — the
TEST asserts the entries exist but does NOT assert exact numeric pricing.
That keeps the test robust to a refresh-pricing.py run.
"""
from __future__ import annotations

from datetime import date

from localsmartz.cost import LAST_VERIFIED, RATES


def test_deprecated_groq_models_removed():
    assert "mixtral-8x7b-32768" not in RATES
    assert "qwen-2.5-32b" not in RATES


def test_llama_3_1_8b_instant_added():
    rate = RATES.get("llama-3.1-8b-instant")
    assert rate is not None
    assert rate["input_per_1m"] > 0
    assert rate["output_per_1m"] > 0
    assert "groq" in rate["note"].lower()


def test_llama_4_maverick_added():
    rate = RATES.get("meta-llama/llama-4-maverick-17b-128e-instruct")
    assert rate is not None
    assert rate["input_per_1m"] > 0
    assert rate["output_per_1m"] > 0


def test_llama_4_scout_added():
    rate = RATES.get("meta-llama/llama-4-scout-17b-16e-instruct")
    assert rate is not None
    assert rate["input_per_1m"] > 0


def test_groq_compound_added():
    rate = RATES.get("groq/compound")
    assert rate is not None
    assert rate["input_per_1m"] > 0


def test_last_verified_bumped_to_2026_05_08():
    """Sanity check that the rate-card refresh is dated correctly."""
    assert LAST_VERIFIED == date(2026, 5, 8)


def test_existing_groq_70b_preserved():
    """The Llama-3.3-70b-versatile entry is the mid-tier; must still exist."""
    rate = RATES.get("llama-3.3-70b-versatile")
    assert rate is not None
    assert rate["input_per_1m"] == 0.59
    assert rate["output_per_1m"] == 0.79


def test_groq_tier_table_uses_new_models():
    """profiles.CLOUD_TIER_TABLE['groq'] must point at the refreshed lineup."""
    from localsmartz.profiles import CLOUD_TIER_TABLE

    groq = CLOUD_TIER_TABLE["groq"]
    assert groq["cheap"] == "llama-3.1-8b-instant"
    assert groq["mid"] == "llama-3.3-70b-versatile"
    assert groq["strong"] == "meta-llama/llama-4-maverick-17b-128e-instruct"


def test_groq_tier_models_all_in_rates():
    """Every model named in CLOUD_TIER_TABLE['groq'] must have a RATES entry.

    Otherwise cost reporting silently returns rate_known=False at runtime.
    """
    from localsmartz.profiles import CLOUD_TIER_TABLE

    for tier, model in CLOUD_TIER_TABLE["groq"].items():
        assert model in RATES, f"groq tier {tier!r}: {model!r} missing from cost.RATES"
