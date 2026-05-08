# Intent — LLM Provider Optimizations (2026-05-08)

## North Star
Local-Ollama is the default and stays the default. Cloud (Anthropic, OpenAI, Groq) becomes a first-class fallback — the same surfaces work on both, and switching providers is one config flip away.

## Update Intent
Ship 10 sequenced commits across 4 batches that:
1. **R1 — Empirical baseline.** Multi-provider eval scorecard establishes the parity floor.
2. **R2 — Cloud-path hardening.** Prompt caching, real token counts, retry/backoff, parallel worker dispatch.
3. **R3 — UX + safety.** Token streaming, tier-aware routing, advisory budget warn, pricing freshness signal.
4. **R4 — Hygiene.** Error taxonomy, OTel parity, dual-path divergence documentation.

## User Value
- Researcher who runs Local-Smartz on their laptop: faster cloud fallback when Ollama can't keep up; never crashes when keys aren't set; sees real cost (not a 4-chars-per-token guess) when they switch.
- Operator deploying on a small cloud box: parallel orchestrator workers cut wall-clock; retries handle transient cloud blips without a manual re-run.

## Non-Goals
- Do NOT modify the Ollama runner unless strictly necessary.
- Do NOT collapse `_create_model` (DeepAgents) and `create_langchain_model` (patterns) — RunnableRetry is unhashable inside DeepAgents and strips bind_tools (project memory `reference_deepagents_runnable_retry.md`).
- Do NOT introduce new dependencies; cloud SDKs (`anthropic`, `openai`, `langchain-anthropic`, `langchain-openai`) are already in pyproject.toml.

## Hard Constraints
- Tests must pass at every commit (`uv run pytest`). Baseline 674 collected.
- Provider absent (no API key) = skip gracefully, never crash.
- Implementers do NOT commit (single-writer git contract — orchestrator commits sequentially).
- No API keys present in this environment (`anthropic_api_key`, `openai_api_key`, `groq_api_key` all missing). Multi-provider scorecard exercises Ollama only; cloud paths must skip cleanly.
