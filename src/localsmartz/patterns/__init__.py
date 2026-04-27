"""Coordination patterns for multi-agent research.

Each pattern is a provider-agnostic composition of agent turns. Patterns
consume ``AgentRunner`` (``localsmartz.runners``) — they never import
ollama/anthropic/openai directly. This is the core architectural bet from
research doc §Runtime abstraction.

Shipped patterns:

Phase 1/2:
- ``single`` — baseline (equivalent to existing DeepAgents single-agent flow)
- ``chain`` — linear stages (gather → analyze → write)
- ``router`` — classify-then-dispatch to a specialist
- ``critic_loop`` — writer generates → critic scores → refine

Phase 3 (2026-04-23):
- ``reflection`` — primary generates → same-model reflector scores +
  suggests → revise (Ω ≤ 3, stagnation guard)
- ``orchestrator`` — lead decomposes → workers execute independently →
  lead synthesizes from artifact summaries only (F7 telephone-game
  mitigation, one-level-only enforced)
- ``parallel`` — N samples with varied temperature → synthesizer merges
  (tier-gated concurrency, temperature ≥ 0.3 guard)

Deferred to v4: ``debate``.
Deferred to v3: dynamic agent creation.
"""
from __future__ import annotations

from localsmartz.patterns.base import Pattern, PatternEvent
from localsmartz.patterns.chain import run as run_chain
from localsmartz.patterns.critic_loop import run as run_critic_loop
from localsmartz.patterns.orchestrator import run as run_orchestrator
from localsmartz.patterns.parallel import run as run_parallel
from localsmartz.patterns.reflection import run as run_reflection
from localsmartz.patterns.router import run as run_router
from localsmartz.patterns.single import run as run_single


# Registry — name → (description, required_roles, entrypoint).
REGISTRY: dict[str, dict] = {
    "single": {
        "description": "Baseline. One agent, all tools.",
        "required_roles": ["primary"],
        "run": run_single,
    },
    "chain": {
        "description": "Linear stages: gather → analyze → write.",
        "required_roles": ["primary"],
        "run": run_chain,
    },
    "router": {
        "description": "Classify query, dispatch to a specialist.",
        "required_roles": ["router", "specialist"],
        "run": run_router,
    },
    "critic_loop": {
        "description": "Writer generates, critic scores, refine until threshold or budget exhausted.",
        "required_roles": ["writer", "critic"],
        "run": run_critic_loop,
    },
    "reflection": {
        "description": "Self-critique loop. Primary drafts, reflector scores + suggests, primary revises. Ω ≤ 3 with stagnation guard.",
        "required_roles": ["primary", "reflector"],
        "run": run_reflection,
    },
    "orchestrator": {
        "description": "Lead decomposes into workers; workers write artifacts; lead synthesizes from summaries. One-level-only (P1). Requires standard+ tier by default.",
        "required_roles": ["orchestrator", "worker"],
        "run": run_orchestrator,
    },
    "parallel": {
        "description": "N varied-temperature samples → synthesizer merges. Tier-gated concurrency (mini: 1, standard: 2, full: 3).",
        "required_roles": ["sampler", "synthesizer"],
        "run": run_parallel,
    },
}


def get_pattern(name: str) -> dict:
    if name not in REGISTRY:
        raise ValueError(
            f"Unknown pattern: {name!r}. Available: {sorted(REGISTRY.keys())}"
        )
    return REGISTRY[name]


__all__ = [
    "Pattern",
    "PatternEvent",
    "REGISTRY",
    "get_pattern",
    "run_single",
    "run_chain",
    "run_router",
    "run_critic_loop",
    "run_reflection",
    "run_orchestrator",
    "run_parallel",
]
