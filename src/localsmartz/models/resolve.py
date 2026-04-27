"""Role → concrete model resolution with tier-aware substitution.

The pattern + AgentsTab layer asks for a role (``writer``, ``critic``,
``router``, etc.). This module returns a concrete ``ModelRef`` that:

- Honors explicit per-agent overrides from ``global_config.agent_models``
- Falls back to the tier default from the registry
- Forces ``critic = writer`` on the mini tier (F2: model-swap thrash)
"""
from __future__ import annotations

import logging
from typing import Any

from localsmartz.models.registry import Tier, Role, recs_for_role

log = logging.getLogger(__name__)


def _default_for_role(role: Role, tier: Tier) -> str | None:
    """Pick the first registry entry that advertises this role at or below tier."""
    pool = recs_for_role(role, tier)
    if not pool:
        return None
    # Prefer exact-tier matches first.
    exact = [r for r in pool if r.get("tier") == tier]
    return (exact[0] if exact else pool[0])["name"]


def resolve_role_model(
    role: Role,
    tier: Tier,
    config: dict[str, Any] | None = None,
    *,
    co_resident: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    """Resolve a role to a concrete ``ModelRef`` dict.

    ``config`` is the project config dict (may contain
    ``agents[role].model_ref``). ``co_resident`` is the set of
    already-resolved models in this run keyed by role; we use it to enforce
    the mini-tier writer==critic constraint (F2).

    Returns None if no suitable model can be found for the role on this
    tier (caller should surface as a preflight failure).
    """
    config = config or {}
    co_resident = co_resident or {}

    # 1. Explicit per-role override from project config.
    agents_cfg = config.get("agents") or {}
    slot = agents_cfg.get(role) if isinstance(agents_cfg, dict) else None
    if isinstance(slot, dict):
        explicit = slot.get("model_ref")
        if isinstance(explicit, dict) and explicit.get("provider") and explicit.get("name"):
            return dict(explicit)

    # 2. Mini-tier F2: force critic to share writer.
    if tier == "mini" and role == "critic" and "writer" in co_resident:
        log.info("tier=mini forced critic=writer (F2 — mini co-residency)")
        return {"provider": "ollama", "name": co_resident["writer"]}

    # 3. Fall back to registry default for this role+tier.
    name = _default_for_role(role, tier)
    if name:
        return {"provider": "ollama", "name": name}

    return None
