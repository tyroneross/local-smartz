"""ModelRec TypedDict + registry lookups.

The registry is populated by ``catalog.py`` at import time. This module is
just the data schema and the lookup helpers.
"""
from __future__ import annotations

from typing import Literal, TypedDict

Tier = Literal["mini", "standard", "full"]
Role = Literal["fast", "strong", "critic", "router", "vision", "coder", "embed"]
ToolReliability = Literal["strong", "usable", "constrained", "experimental"]
ReasoningMode = Literal["native", "optional", "off-by-default"]


class ModelRec(TypedDict, total=False):
    """Per-model metadata. See research doc §Model recommendation data model."""
    name: str                       # canonical Ollama tag, e.g. "qwen3.5:27b"
    family: str                     # "qwen3.5" — for de-dup across sizes
    params_b: float                 # 27.0
    size_gb_q4: float               # 20.0 — RAM budget estimate at Q4_K_M
    tier: Tier                      # which hardware tier this is sized for
    roles: list[Role]               # assignments this model is suitable for
    tool_reliability: ToolReliability
    reasoning_mode: ReasoningMode   # "off-by-default" triggers F22 guard
    capabilities: list[str]         # ["tools","vision","thinking"]
    context_window: int             # 131072
    notes: str                      # free-form UI hint
    default_for: list[str]          # e.g. ["critic_loop.writer", "orchestrator.worker"]


# Registry populated by catalog.py on import (module-level side-effect is OK
# here — it's just a dict literal).
_BY_NAME: dict[str, ModelRec] = {}


def register(rec: ModelRec) -> None:
    """Register one model recommendation. Idempotent on name."""
    _BY_NAME[rec["name"]] = rec


def get_model_rec(name: str) -> ModelRec | None:
    """Return the ModelRec for a canonical Ollama tag, or None if unregistered.

    Best-effort fuzzy: also tries stripping the quant suffix
    (``qwen3.5:9b-q4_K_M`` → ``qwen3.5:9b``) so callers can pass pulled-tag
    strings and still hit the registry.
    """
    if not name:
        return None
    if name in _BY_NAME:
        return _BY_NAME[name]
    # Strip a trailing quant suffix like "-q4_K_M"
    if "-" in name and ":" in name:
        base, _, _tail = name.partition("-")
        # Only strip if the tail looks like a quant marker.
        if _tail.lower().startswith(("q", "iq", "f16", "f32", "bf16")):
            return _BY_NAME.get(base)
    return None


def get_all_recs() -> list[ModelRec]:
    return list(_BY_NAME.values())


def recs_for_tier(tier: Tier) -> list[ModelRec]:
    """Models suitable for a given hardware tier.

    Mini gets mini-only. Standard and full inherit smaller tiers — a 64GB
    machine can run everything a 24GB machine can.
    """
    order = {"mini": 0, "standard": 1, "full": 2}
    max_level = order.get(tier, 0)
    return [
        r for r in _BY_NAME.values()
        if order.get(r.get("tier", "mini"), 0) <= max_level
    ]


def recs_for_role(role: Role, tier: Tier | None = None) -> list[ModelRec]:
    """Models that advertise support for a given role (optionally tier-gated)."""
    pool = recs_for_tier(tier) if tier else list(_BY_NAME.values())
    return [r for r in pool if role in (r.get("roles") or [])]
