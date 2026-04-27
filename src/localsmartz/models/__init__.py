"""Model catalog, registry, install, resolve, and preflight.

One module surface for everything model-related. UI, CLI, and pattern runner
all call through here so we have one install function, one progress stream,
one telemetry span (``ls.model.install``).

Exports:
- ModelRec (registry.py) — the per-model TypedDict
- get_model_rec(name) / get_all_recs() / recs_for_tier(tier)
- install.install(name, ...) — shared with HTTP SSE and CLI
- resolve.resolve_role_model(role, tier, config) — tier-aware substitution
- preflight.check(pattern, profile) — block pattern open until models present
"""
from __future__ import annotations

from localsmartz.models.registry import (
    ModelRec,
    get_model_rec,
    get_all_recs,
    recs_for_tier,
    recs_for_role,
)
from localsmartz.models.catalog import (
    CATALOG,
    recommended_for_tier,
)
from localsmartz.models import install, resolve, preflight

__all__ = [
    "ModelRec",
    "get_model_rec",
    "get_all_recs",
    "recs_for_tier",
    "recs_for_role",
    "CATALOG",
    "recommended_for_tier",
    "install",
    "resolve",
    "preflight",
]
