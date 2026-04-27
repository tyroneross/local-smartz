"""Tier-matched recommendation catalog.

Exact mapping from research doc §Tier-matched recommendations (2026-04-23):

- mini  (24GB):   qwen3.5:9b (fast+strong), qwen3.5:4b (tiny router, optional)
- standard (64):  qwen3.5:9b (fast), qwen3.5:27b (strong), critic shares 9B
                  plus qwen3-coder-next:30b (coder), gemma4:26b (vision alt)
- full (128+):    qwen3.5:9b (fast), qwen3.5:122b (strong), qwen3-next:80b (reasoning critic)
- cross-tier:     qwen3-coder-next:30b, lfm2.5-thinking:1.2b, bge-base-en-v1.5

CRITICAL: qwen3.5 family ships ``reasoning_mode: "off-by-default"`` — the
local runner auto-injects ``reasoning: false`` to prevent F22 (tool-call
JSON mangling when reasoning is on).
"""
from __future__ import annotations

from localsmartz.models.registry import ModelRec, register


# NOTE: Ollama canonical tags change over time. These are the tags shown on
# ollama.com/library as of 2026-04-23 per research doc. Users pulling newer
# variants (e.g. ``qwen3.5:9b-instruct-q4_K_M``) will still hit the registry
# via ``get_model_rec``'s quant-stripping fuzzy lookup.

CATALOG: list[ModelRec] = [
    # ── Mini tier (24GB) ──────────────────────────────────────────────────
    {
        "name": "qwen3.5:9b",
        "family": "qwen3.5",
        "params_b": 9.0,
        "size_gb_q4": 6.0,
        "tier": "mini",
        "roles": ["fast", "strong", "critic", "router"],
        "tool_reliability": "usable",
        "reasoning_mode": "off-by-default",
        "capabilities": ["tools", "vision", "thinking"],
        "context_window": 131072,
        "notes": "Best tool-call reliability in its size class. Reasoning forced off for stable JSON tool calls.",
        "default_for": ["single.primary", "chain.*", "router.specialist", "critic_loop.writer", "critic_loop.critic"],
    },
    {
        "name": "qwen3.5:4b",
        "family": "qwen3.5",
        "params_b": 4.0,
        "size_gb_q4": 3.0,
        "tier": "mini",
        "roles": ["fast", "router"],
        "tool_reliability": "usable",
        "reasoning_mode": "off-by-default",
        "capabilities": ["tools", "thinking"],
        "context_window": 131072,
        "notes": "Tiny enough to co-resident with 9B. Optional router slot.",
        "default_for": ["router.classifier"],
    },
    # ── Standard tier (64GB) ──────────────────────────────────────────────
    {
        "name": "qwen3.5:27b",
        "family": "qwen3.5",
        "params_b": 27.0,
        "size_gb_q4": 20.0,
        "tier": "standard",
        "roles": ["strong", "critic", "vision"],
        "tool_reliability": "strong",
        "reasoning_mode": "off-by-default",
        "capabilities": ["tools", "vision", "thinking"],
        "context_window": 131072,
        "notes": "General-purpose strong executor. Reasoning off for reliable tool calls.",
        "default_for": ["single.primary.standard", "critic_loop.critic.standard"],
    },
    {
        "name": "qwen3-coder-next:30b",
        "family": "qwen3-coder-next",
        "params_b": 30.0,
        "size_gb_q4": 22.0,
        "tier": "standard",
        "roles": ["strong", "coder"],
        "tool_reliability": "strong",
        "reasoning_mode": "optional",
        "capabilities": ["tools"],
        "context_window": 131072,
        "notes": "Swap in for code-heavy projects.",
        "default_for": ["coder.*"],
    },
    {
        "name": "gemma4:26b",
        "family": "gemma4",
        "params_b": 26.0,
        "size_gb_q4": 19.0,
        "tier": "standard",
        "roles": ["strong", "vision"],
        "tool_reliability": "strong",
        "reasoning_mode": "native",
        "capabilities": ["tools", "vision", "thinking"],
        "context_window": 131072,
        "notes": "Long-context vision alternative with native function calling.",
        "default_for": ["vision.*"],
    },
    # ── Full tier (128GB+) ────────────────────────────────────────────────
    {
        "name": "qwen3.5:122b",
        "family": "qwen3.5",
        "params_b": 122.0,
        "size_gb_q4": 70.0,
        "tier": "full",
        "roles": ["strong"],
        "tool_reliability": "strong",
        "reasoning_mode": "off-by-default",
        "capabilities": ["tools", "vision", "thinking"],
        "context_window": 131072,
        "notes": "Frontier-adjacent local quality. Heavyweight strong executor.",
        "default_for": ["single.primary.full"],
    },
    {
        "name": "qwen3-next:80b",
        "family": "qwen3-next",
        "params_b": 80.0,
        "size_gb_q4": 48.0,
        "tier": "full",
        "roles": ["critic", "strong"],
        "tool_reliability": "strong",
        "reasoning_mode": "native",
        "capabilities": ["tools", "thinking"],
        "context_window": 131072,
        "notes": "Dedicated thinking model for hard critic / debate judge.",
        "default_for": ["critic_loop.critic.full"],
    },
    # ── gpt-oss family (OpenAI open-weights, standard + full) ─────────────
    {
        "name": "gpt-oss:20b",
        "family": "gpt-oss",
        "params_b": 20.0,
        "size_gb_q4": 13.0,
        "tier": "standard",
        "roles": ["strong", "critic"],
        "tool_reliability": "usable",
        "reasoning_mode": "native",
        "capabilities": ["tools", "thinking"],
        "context_window": 131072,
        "notes": "OpenAI open-weights 20B; standard-tier alternative to qwen3.5:27b.",
        "default_for": [],
    },
    {
        "name": "gpt-oss:120b",
        "family": "gpt-oss",
        "params_b": 120.0,
        "size_gb_q4": 65.0,
        "tier": "full",
        "roles": ["strong"],
        "tool_reliability": "usable",
        "reasoning_mode": "native",
        "capabilities": ["tools", "thinking"],
        "context_window": 131072,
        "notes": "OpenAI open-weights 120B; full-tier alternative to qwen3.5:122b.",
        "default_for": [],
    },
    # ── qwen3-vl (vision specialist, all tiers) ───────────────────────────
    {
        "name": "qwen3-vl:2b",
        "family": "qwen3-vl",
        "params_b": 2.0,
        "size_gb_q4": 1.6,
        "tier": "mini",
        "roles": ["vision", "fast"],
        "tool_reliability": "usable",
        "reasoning_mode": "off-by-default",
        "capabilities": ["tools", "vision", "thinking"],
        "context_window": 131072,
        "notes": "Ultra-compact vision-language model; fits as co-resident vision on 24GB.",
        "default_for": [],
    },
    {
        "name": "qwen3-vl:32b",
        "family": "qwen3-vl",
        "params_b": 32.0,
        "size_gb_q4": 22.0,
        "tier": "standard",
        "roles": ["vision", "strong"],
        "tool_reliability": "strong",
        "reasoning_mode": "off-by-default",
        "capabilities": ["tools", "vision", "thinking"],
        "context_window": 131072,
        "notes": "Qwen vision family standard-tier flagship. Prefer over gemma4:26b for strict-JSON vision pipelines.",
        "default_for": ["vision.standard"],
    },
    # ── Cross-tier specialists ────────────────────────────────────────────
    {
        "name": "lfm2.5-thinking:1.2b",
        "family": "lfm2.5-thinking",
        "params_b": 1.2,
        "size_gb_q4": 1.0,
        "tier": "mini",
        "roles": ["router", "fast"],
        "tool_reliability": "constrained",
        "reasoning_mode": "native",
        "capabilities": ["tools"],
        "context_window": 32768,
        "notes": "Ultra-fast router / classifier at any tier.",
        "default_for": [],
    },
    # ── Embeddings (modernized 2026-04-23) ────────────────────────────────
    # qwen3-embedding supersedes bge for retrieval in new projects; nomic
    # kept as battle-tested fallback (66M pulls, widest ecosystem).
    {
        "name": "qwen3-embedding:0.6b",
        "family": "qwen3-embedding",
        "params_b": 0.6,
        "size_gb_q4": 0.5,
        "tier": "mini",
        "roles": ["embed"],
        "tool_reliability": "experimental",
        "reasoning_mode": "optional",
        "capabilities": ["embedding"],
        "context_window": 8192,
        "notes": "Compact embedding for retrieval at any tier.",
        "default_for": ["embed.mini"],
    },
    {
        "name": "qwen3-embedding:8b",
        "family": "qwen3-embedding",
        "params_b": 8.0,
        "size_gb_q4": 5.0,
        "tier": "standard",
        "roles": ["embed"],
        "tool_reliability": "experimental",
        "reasoning_mode": "optional",
        "capabilities": ["embedding"],
        "context_window": 8192,
        "notes": "Strong retrieval quality; pairs with qwen-family primary models.",
        "default_for": ["embed.standard", "embed.full"],
    },
    {
        "name": "embeddinggemma:300m",
        "family": "embeddinggemma",
        "params_b": 0.3,
        "size_gb_q4": 0.3,
        "tier": "mini",
        "roles": ["embed"],
        "tool_reliability": "experimental",
        "reasoning_mode": "optional",
        "capabilities": ["embedding"],
        "context_window": 8192,
        "notes": "Minimum-footprint embedding option for mini-tier retrieval.",
        "default_for": [],
    },
    {
        "name": "nomic-embed-text",
        "family": "nomic-embed-text",
        "params_b": 0.1,
        "size_gb_q4": 0.3,
        "tier": "mini",
        "roles": ["embed"],
        "tool_reliability": "experimental",
        "reasoning_mode": "optional",
        "capabilities": ["embedding"],
        "context_window": 8192,
        "notes": "Battle-tested embedding fallback; 66M pulls, widest ecosystem support.",
        "default_for": [],
    },
    {
        "name": "bge-base-en-v1.5",
        "family": "bge-base-en-v1.5",
        "params_b": 0.1,
        "size_gb_q4": 0.5,
        "tier": "mini",
        "roles": ["embed"],
        "tool_reliability": "experimental",
        "reasoning_mode": "optional",
        "capabilities": ["embedding"],
        "context_window": 512,
        "notes": "Legacy embedding — kept for backward compatibility; new projects should use qwen3-embedding.",
        "default_for": [],
    },
]


# Register on import so ``registry.get_model_rec`` works everywhere.
for _rec in CATALOG:
    register(_rec)


def recommended_for_tier(tier: str) -> list[ModelRec]:
    """Return the recommended install set for a fresh machine at the tier.

    Mini gets the minimum viable set (1 primary + optional router).
    Standard adds a 27B strong executor.
    Full adds the 122B + 80B reasoning critic.

    gemma4:26b is included at standard+ as a native-reasoning vision-capable
    strong-tool-call alternative; smaller than qwen3.5:122b (faster
    co-residency) and complements the gpt-oss family.
    """
    if tier == "mini":
        names = ["qwen3.5:9b"]
    elif tier == "standard":
        names = ["qwen3.5:9b", "qwen3.5:27b", "gemma4:26b"]
    elif tier == "full":
        names = ["qwen3.5:9b", "qwen3.5:122b", "qwen3-next:80b", "gemma4:26b"]
    else:
        names = ["qwen3.5:9b"]

    return [r for r in CATALOG if r["name"] in names]
