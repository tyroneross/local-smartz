"""Tier-aware task routing tests (feat: c7)."""
from __future__ import annotations

import pytest

from localsmartz.profiles import (
    AGENT_ROLES,
    CLOUD_TIER_TABLE,
    DEFAULT_TIER,
    TIER_BY_ROLE,
    resolve_model_for_role,
    tier_for_role,
)


def test_tier_table_covers_all_roles():
    """Every AGENT_ROLES key must have a tier assignment."""
    missing = set(AGENT_ROLES.keys()) - set(TIER_BY_ROLE.keys())
    assert not missing, f"AGENT_ROLES without tier assignment: {missing}"


def test_tier_table_values_are_valid():
    """All tiers must be one of cheap/mid/strong."""
    for role, tier in TIER_BY_ROLE.items():
        assert tier in {"cheap", "mid", "strong"}, f"role {role} has invalid tier {tier}"


def test_tier_for_role_known():
    assert tier_for_role("writer") == "strong"
    assert tier_for_role("researcher") == "cheap"
    assert tier_for_role("planner") == "mid"


def test_tier_for_role_unknown_defaults_mid():
    assert tier_for_role("does-not-exist") == DEFAULT_TIER
    assert DEFAULT_TIER == "mid"


def test_resolve_anthropic_writer_strong():
    assert resolve_model_for_role("writer", "anthropic") == "claude-opus-4-7"


def test_resolve_anthropic_researcher_cheap():
    assert resolve_model_for_role("researcher", "anthropic") == "claude-haiku-4"


def test_resolve_groq_tiered_models():
    """Groq tier table (refreshed 2026-05-08): cheap=8b-instant, mid=70b-versatile,
    strong=openai/gpt-oss-120b (Maverick deprecated on Groq 2026-05-08)."""
    # writer → strong tier
    assert resolve_model_for_role("writer", "groq") == "openai/gpt-oss-120b"
    # researcher → cheap tier
    assert resolve_model_for_role("researcher", "groq") == "llama-3.1-8b-instant"
    # planner → mid tier
    assert resolve_model_for_role("planner", "groq") == "llama-3.3-70b-versatile"


def test_resolve_openai_strong_vs_cheap():
    assert resolve_model_for_role("writer", "openai") == "gpt-4o"
    assert resolve_model_for_role("researcher", "openai") == "gpt-4o-mini"


def test_resolve_unknown_role_uses_mid():
    """Unknown role → default tier (mid)."""
    assert resolve_model_for_role("nope", "anthropic") == "claude-sonnet-4-6"


def test_resolve_unknown_provider_raises():
    with pytest.raises(ValueError) as exc:
        resolve_model_for_role("writer", "cohere")
    assert "cohere" in str(exc.value)
    assert "anthropic" in str(exc.value)  # lists known providers


def test_cloud_table_models_are_in_cost_rates():
    """Every model in CLOUD_TIER_TABLE must have an entry in cost.RATES."""
    from localsmartz.cost import RATES

    seen = set()
    for table in CLOUD_TIER_TABLE.values():
        for model in table.values():
            seen.add(model)
    missing = seen - set(RATES.keys())
    assert not missing, f"Tier table has models without cost.RATES entries: {missing}"


def test_existing_agent_roles_dict_unchanged():
    """c7 must not modify AGENT_ROLES."""
    expected = {"planner", "researcher", "analyzer", "writer", "fact_checker", "orchestrator"}
    assert set(AGENT_ROLES.keys()) == expected
