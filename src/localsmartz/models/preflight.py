"""Pattern preflight — block pattern run until required role models are present.

Called by the UI (``GET /api/patterns/<name>/preflight``) and CLI (``localsmartz
model doctor``) before a pattern can be selected / run. Returns:

    {
      "ok": bool,
      "missing": [{"role": str, "model_ref": ModelRef, "install_cmd": str}, ...],
      "tier_warnings": [{"role": str, "message": str}, ...],
      "suggestions": [...]
    }

``ok`` is True when every required role has a pulled model. UI shows a
one-click install CTA for each missing model.
"""
from __future__ import annotations

from typing import Any

from localsmartz.models.registry import get_model_rec
from localsmartz.ollama import model_available


# Required roles per pattern. Kept flat (not DAG) — patterns today don't
# nest. Extend as new patterns ship.
PATTERN_REQUIRED_ROLES: dict[str, list[str]] = {
    "single": ["primary"],
    "chain": ["primary"],  # one model runs every stage on mini by default
    "router": ["router", "specialist"],
    "critic_loop": ["writer", "critic"],
    # Phase 3 patterns
    "reflection": ["primary", "reflector"],
    "orchestrator": ["orchestrator", "worker"],
    "parallel": ["sampler", "synthesizer"],
}


def _map_role_to_registry_role(role: str) -> str:
    """Map a pattern slot role to a registry ``Role`` value.

    The registry uses abstract roles (``fast``, ``strong``, ``critic``,
    ``router``); patterns use slot roles (``writer``, ``specialist``,
    ``primary``). This table keeps them aligned.
    """
    return {
        "primary": "strong",
        "writer": "strong",
        "critic": "critic",
        "router": "router",
        "specialist": "strong",
        # Phase 3 slot roles
        "reflector": "critic",       # reflector is a critic-shape scorer
        "orchestrator": "strong",    # lead decomposes + synthesizes
        "worker": "strong",          # same shape as specialist
        "sampler": "strong",
        "synthesizer": "strong",
    }.get(role, role)


def check(pattern: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Preflight: can this pattern run on this profile right now?

    ``profile`` is a dict with at minimum ``tier`` and ``name``; we also
    accept the legacy ``profile["agents"]`` shape for backward compat.
    """
    from localsmartz.models.resolve import resolve_role_model

    tier = profile.get("tier") or (
        "full" if profile.get("name") == "full" else "mini"
    )

    required = PATTERN_REQUIRED_ROLES.get(pattern, [])
    config = profile.get("config", profile)

    missing: list[dict[str, Any]] = []
    tier_warnings: list[dict[str, Any]] = []
    co_resident: dict[str, str] = {}

    for role in required:
        registry_role = _map_role_to_registry_role(role)
        ref = resolve_role_model(registry_role, tier, config, co_resident=co_resident)
        if not ref:
            missing.append(
                {
                    "role": role,
                    "model_ref": None,
                    "install_cmd": None,
                    "reason": f"no registry entry for role={registry_role} on tier={tier}",
                }
            )
            continue

        name = ref["name"]
        co_resident[role] = name

        # Only local Ollama models need a pull check — cloud refs are handled
        # by the cloud provider at runtime (API key check).
        if ref.get("provider") == "ollama" and not model_available(name):
            missing.append(
                {
                    "role": role,
                    "model_ref": ref,
                    "install_cmd": f"localsmartz model add {name}",
                    "reason": "not pulled",
                }
            )

        # Tier mismatch warning (registry tier > project tier).
        rec = get_model_rec(name)
        if rec:
            order = {"mini": 0, "standard": 1, "full": 2}
            if order.get(rec.get("tier", "mini"), 0) > order.get(tier, 0):
                tier_warnings.append(
                    {
                        "role": role,
                        "message": (
                            f"Model {name} is sized for tier={rec['tier']} "
                            f"but you're on {tier}. Expect latency / swap thrash."
                        ),
                    }
                )

    return {
        "ok": not missing,
        "missing": missing,
        "tier_warnings": tier_warnings,
    }
