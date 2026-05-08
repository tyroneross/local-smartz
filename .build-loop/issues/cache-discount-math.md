# Deferred: Anthropic cache discount math in cost.cost_from_usage

**Status:** Open / informational
**Owner:** TBD
**Filed:** 2026-05-08 (c3 of llm-provider-optimizations batch)

## Context
`cost.cost_from_usage` surfaces `cache_creation_input_tokens` and
`cache_read_input_tokens` verbatim from the SDK response, but does NOT
apply Anthropic's cache pricing discount when computing `estimated_usd`.

## What Anthropic charges (subject to verification — see pricing page)
- Standard input tokens: full rate
- Cache writes (`cache_creation_input_tokens`): ~1.25× input rate (premium)
- Cache reads (`cache_read_input_tokens`): ~0.10× input rate (90% discount)

## Why deferred in c3
1. The c9 commit's freshness signal is the right place to verify these
   coefficients before encoding them.
2. Surfacing the raw counts is the high-value change; downstream consumers
   (UI, billing dashboards) can apply the math from a single shared
   helper later.
3. Avoids encoding pricing constants that drift faster than the per-1M
   rates already do.

## Suggested follow-up
Add `CACHE_WRITE_MULTIPLIER` and `CACHE_READ_MULTIPLIER` next to RATES,
verified against anthropic.com/pricing, and update `cost_from_usage`'s
billing math accordingly. Also surface `effective_input_tokens` for
clarity. Track via a future commit; not blocking the current batch.
