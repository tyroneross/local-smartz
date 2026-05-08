# 2026-05-08 — Groq integration follow-ups

Build-loop plan covering 5 commits to close out yesterday's 10-commit run
(b5c01f9..57e219d). Builds on the verified eval baseline at
`.build-loop/evals/2026-05-08-multi-provider-parity.md` (Groq llama-3.1-8b-instant
1.000 / 300ms p50, gpt-oss:20b 1.000 / 1234ms, llama-3.3-70b-versatile 0.833 / 304ms).

## Goal

Land the open Groq follow-ups + the two cloud-runner SSE deferrals (c6 + c8)
+ the Anthropic cache-discount math (c3) as five separate commits. Single-writer
git contract — orchestrator commits, implementers do not.

## Constraints (hard)

- `uv run pytest` green at every commit (baseline 779).
- Cloud SDKs remain runtime-optional — no new mandatory deps.
- Don't collapse `_create_model` / `create_langchain_model` (DeepAgents
  RunnableRetry constraint, see project memory).
- Don't touch unrelated pre-existing `M` files: `HANDOFF.md`, `README.md`,
  `app/project.yml`, `scripts/ui-smoke.sh`, `uv.lock`, `.build-loop/state.json`,
  `.build-loop/goal.md`, `app/LocalSmartz/Info.plist`.
- Implementer envelopes return `commit_subject` + `commit_body` + `files_changed`;
  orchestrator stages and commits sequentially per the build-loop single-writer
  git contract.

## Synthesis dimensions

(Used by the C3 attestation lint and C4 synthesis-critic.)

- `placement` — n/a (no UI files in any commit)
- `cta_tier` — n/a
- `visual_weight` — n/a
- `copy_tone` — n/a
- `empty_state` — n/a

This batch is backend-only; UI gates skip cleanly.

## Commits

### Commit A — `feat(secrets): add Groq preset; refresh multi-provider parity scorecard`

- **Size**: XS
- **Tier**: default fan-out (mechanical)
- **files_owned**:
  - `src/localsmartz/secrets.py` (already modified on disk — Groq added to `PRESET_PROVIDERS`, docstring fixed)
  - `tests/test_secrets.py` (already modified — Groq replaces Serper in test)
  - `.build-loop/evals/2026-05-08-multi-provider-parity.md` (regenerated scorecard)
  - `.build-loop/evals/2026-05-08-multi-provider-parity.tsv` (regenerated TSV)
- **modifies_api**: no
- **F-criteria**:
  - `uv run pytest tests/test_secrets.py -q` passes (was 11/11 pre-staged)
  - `secrets.is_set("Groq") == True` (already validated locally)
- **Risk**: trivial — files already exist on disk and have been validated.

### Commit B — `fix(model_eval): resolve keys via export_to_env; refresh Groq model lineup`

- **Size**: S
- **Tier**: default fan-out
- **Bug**: `src/localsmartz/model_eval.py:331` calls `_secrets.get(f"{provider}_api_key")`
  (e.g. `"groq_api_key"`) — but `PRESET_PROVIDERS` stores under canonical
  capitalized names (`"Groq"`, `"Anthropic"`, `"OpenAI"`). `_provider_has_key`
  silently misses the Keychain entry and falls through to "no API key".
- **Fix path**: at the top of `main_multi_provider`, call
  `localsmartz.secrets.export_to_env()` once. The existing env-var check at
  L326 then catches every backend correctly. Drop the broken Keychain branch
  (L328-334) — `export_to_env` makes it redundant. Tests assert the env-var
  path lights up after the call.
- **Pricing refresh** (verify current rates against `https://console.groq.com/docs/models`
  before encoding — pricing entries below are TAG:UNVERIFIED until refresh script confirms):
  - Drop `mixtral-8x7b-32768`, `qwen-2.5-32b` (deprecated per Groq production list).
  - Add `llama-3.1-8b-instant`: input ~$0.05/1M, output ~$0.08/1M.
  - Add `meta-llama/llama-4-maverick-17b-128e-instruct`: vision + function + JSON + 128K (verify pricing).
  - Add `meta-llama/llama-4-scout-17b-16e-instruct` (verify pricing).
  - Add `groq/compound` (verify pricing).
  - Bump `LAST_VERIFIED = date(2026, 5, 8)`.
- **Tier table** (`profiles.CLOUD_TIER_TABLE["groq"]`):
  - `cheap = "llama-3.1-8b-instant"`
  - `mid = "llama-3.3-70b-versatile"`
  - `strong = "meta-llama/llama-4-maverick-17b-128e-instruct"` (user-picked: vision+function+JSON+128K).
- **files_owned**:
  - `src/localsmartz/model_eval.py`
  - `src/localsmartz/cost.py`
  - `src/localsmartz/profiles.py`
  - `tests/test_model_eval_multi_provider.py` (new test asserting `export_to_env` is called and Keychain-stored Groq key is picked up)
  - `tests/test_cost_groq_pricing.py` (new test asserting new model rates exist)
  - `tests/test_profiles.py` (extend if Groq tier table is asserted there)
  - `.build-loop/evals/2026-05-08-multi-provider-parity.md` (refresh after fix)
  - `.build-loop/evals/2026-05-08-multi-provider-parity.tsv` (refresh)
- **modifies_api**: yes — `cost.RATES` keys change. Callers: model_eval, profiles, cost reports. All three live in the same package; sole-consumer pattern, no external API contract.
- **F-criteria**:
  - `uv run pytest -q` green (baseline 779 + new tests).
  - Re-running `localsmartz-model-eval --multi-provider` with only Keychain Groq key (no env var) no longer skips `groq` (validates the fix).
  - Scorecard delta documented in commit body.
- **Risk**: pricing accuracy — verify against Groq pricing page. Tier table change affects cloud-fallback routing for Groq.

### Commit C — `feat(serve): wire _emit_budget_warn into cloud-runner SSE flow (pattern-layer)`

- **Size**: S
- **Tier**: default fan-out
- **Context**: `_emit_budget_warn` helper + SSE event schema landed in c8 (commit 824b1cb)
  but had no callers. Cloud-runner `run_turn` calls happen inside the
  `localsmartz.patterns/*` modules (single, chain, router, critic_loop, parallel,
  reflection, orchestrator), not directly in `serve.py`. Patterns already yield
  `PatternEvent` dicts via `AsyncIterator`, so the budget warn fits as a new
  `PatternEvent.type = "budget_warn"`.
- **Implementation**:
  - Extend `PatternEvent` total set to include `"budget_warn"` (typing only).
  - Add a small helper `localsmartz.patterns.base._maybe_budget_warn(usage_so_far, threshold, provider)`
    that mirrors `serve._emit_budget_warn`'s logic (reuse the existing helper —
    import it, don't re-derive).
  - In each pattern's `run()`, accumulate `usage.input_tokens + usage.output_tokens`
    after each `runner.run_turn(...)` into a `session_tokens` local. After
    accumulating, call `_maybe_budget_warn`; if it returns a dict, yield it once
    per session (track a `warned: bool` flag on a closure or ctx so re-crossings
    don't re-fire).
  - Read `threshold` from `_read_budget_threshold(...)` once at pattern start;
    extract provider from `model_ref["provider"]`. Ollama-only patterns are
    auto-exempted by the helper (provider=="ollama" → None).
- **files_owned**:
  - `src/localsmartz/patterns/base.py` (extend type + add `_maybe_budget_warn` thin wrapper)
  - `src/localsmartz/patterns/single.py`
  - `src/localsmartz/patterns/chain.py`
  - `src/localsmartz/patterns/router.py`
  - `src/localsmartz/patterns/critic_loop.py`
  - `src/localsmartz/patterns/reflection.py`
  - `src/localsmartz/patterns/parallel.py`
  - `src/localsmartz/patterns/orchestrator.py`
  - `tests/test_pattern_budget_warn.py` (new — fake runner + mocked threshold; assert one event fires per session)
- **modifies_api**: no — `PatternEvent` is a TypedDict with `total=False`; adding a `"budget_warn"` event type is additive. Existing event consumers ignore unknown event types.
- **F-criteria**:
  - `uv run pytest -q` green.
  - New test asserts: with threshold=100, two run_turn calls each returning 60+60 tokens fire exactly ONE budget_warn event in the pattern's event stream.
  - Ollama-only run_turn does NOT fire budget_warn (provider exemption preserved).
- **Risk**: 7 patterns × 1 instrumentation site each = repetition. Keep the wrapper helper concentrated so the sites stay 2-line additions.

### Commit D — `feat(patterns): bridge cloud-runner token deltas via stream_turn`

- **Size**: M
- **Tier**: default fan-out
- **Context**: c6 (commit 8cbfd91) added `stream_turn` + `StreamChunk` to
  cloud runners and `runners/base.py`, but no caller invokes it. Patterns
  still go through `run_turn` (whole-turn flush). Wire `stream_turn` into
  the simplest pattern (`single`) so token deltas surface as `PatternEvent`
  events.
- **Implementation**:
  - In `patterns/single.py`, check `hasattr(runner, "stream_turn")` (preserves
    fallback for runners without streaming, e.g. local_ollama, harmony).
  - When streaming is available: iterate `runner.stream_turn(...)`, yielding
    `{"type": "text_delta", "role": "primary", "delta": chunk["delta"]}`
    for each `done=False` chunk. On the terminal `done=True` chunk, yield
    the standard `turn` event built from `chunk["final"]`.
  - When streaming is NOT available: fall back to `run_turn` (current behavior).
  - Extend `PatternEvent` typing to include `text_delta` + `delta` keys.
  - Patterns beyond `single` (chain, router, critic_loop, parallel, reflection,
    orchestrator) intentionally stay on `run_turn` for this commit — those
    patterns interleave multiple roles + tool calls per turn, where the
    streaming bridge is non-trivial. Surface in commit body as scope.
- **End-to-end SSE bridge to `serve.py`**: ❓ Unfixed scoped to this commit.
  `serve.py._stream_research` does NOT currently route to `localsmartz.patterns.*`
  (it only dispatches `_run_fast_path` / `_run_graph_pipeline` /
  `_run_full_agent`, all Ollama-bound). Wiring serve→patterns end-to-end is
  out of scope for this batch; surface as ❓ Unfixed in Review-F. The runner-side
  primitive exists; the pattern-layer bridge is now in place; the serve→pattern
  dispatch is the missing seam.
- **files_owned**:
  - `src/localsmartz/patterns/base.py` (PatternEvent typing extension)
  - `src/localsmartz/patterns/single.py`
  - `tests/test_pattern_single_stream.py` (new — fake runner with stream_turn yielding 3 deltas; assert pattern emits 3 text_delta events + 1 turn event)
- **modifies_api**: no — additive `text_delta` event type; existing consumers ignore.
- **F-criteria**:
  - `uv run pytest -q` green.
  - New test: 3-delta stream produces 3 `text_delta` events + 1 `turn` event + 1 `done` event.
  - Fallback test: runner without `stream_turn` still emits 1 `turn` + 1 `done` event (no regression).
- **Risk**: streaming-iteration in async pattern code is the trickiest part;
  test coverage of both paths is the gate.

### Commit E — `fix(cost): apply Anthropic cache-read discount + cache-write surcharge`

- **Size**: S
- **Tier**: default fan-out
- **Context**: tracked at `.build-loop/issues/cache-discount-math.md`, deferred from c3.
- **Implementation**:
  - Add module constants in `cost.py`:
    `CACHE_WRITE_MULTIPLIER = 1.25`, `CACHE_READ_MULTIPLIER = 0.10`.
  - In `cost_from_usage`, when computing `cost`:
    - `effective_input_tokens = input_tokens - cache_creation - cache_read`
      (the ones billed at full rate)
    - `cache_write_cost = cache_creation * rate["input_per_1m"] * CACHE_WRITE_MULTIPLIER / 1_000_000`
    - `cache_read_cost = cache_read * rate["input_per_1m"] * CACHE_READ_MULTIPLIER / 1_000_000`
    - `output_cost = output_tokens * rate["output_per_1m"] / 1_000_000`
    - `cost = (effective_input_tokens * rate["input_per_1m"] / 1_000_000) + cache_write_cost + cache_read_cost + output_cost`
  - Surface `effective_input_tokens` in the returned envelope for clarity.
  - Verify multipliers against `https://www.anthropic.com/pricing` before
    encoding (TAG:UNVERIFIED until verified).
  - Drop `.build-loop/issues/cache-discount-math.md`.
- **files_owned**:
  - `src/localsmartz/cost.py`
  - `tests/test_cost_cache_discount.py` (new — assert that with cache_read=1000, the cost is 10% of what 1000 normal input tokens would cost; assert cache_write=1000 is 125% of normal)
  - `.build-loop/issues/cache-discount-math.md` (delete)
- **modifies_api**: no breaking change to envelope shape — existing fields preserved + one new field (`effective_input_tokens`). Callers: `cost_from_usage` is consumed by `serve.py` (one call site).
- **F-criteria**:
  - `uv run pytest -q` green.
  - New test asserts: `cost_from_usage(usage={input_tokens: 2000, output_tokens: 0, cache_read_input_tokens: 1000})` returns a cost roughly 10% lower than `cost_from_usage(usage={input_tokens: 2000, output_tokens: 0})` for an Anthropic model.
- **Risk**: pricing constants drift. Mitigated by referencing pricing-freshness signal (c9).

## Caller Audit (Scope Auditor)

Trace of cross-file callers for every modifies_api commit (B and E only — A/C/D
are non-API-contract changes).

### Commit B — `cost.RATES` shape

- `cost.RATES` lookups in repo:
  - `src/localsmartz/cost.py` (definition + usage in `estimate_cost_usd`, `cost_from_usage`)
  - `src/localsmartz/profiles.py` (`CLOUD_TIER_TABLE` references model names that must exist in `RATES` — drop of `mixtral-8x7b-32768` and `qwen-2.5-32b` is safe because `CLOUD_TIER_TABLE["groq"]` doesn't reference them; only `llama-3.3-70b-versatile` was used)
  - `src/localsmartz/model_eval.py` `DEFAULT_CLOUD_MODELS["groq"]` references `llama-3.3-70b-versatile` — preserved.
  - Tests: `tests/test_cost*.py` may reference specific model names → audit and update.
- All callers absorbed in `files_owned`. ✅

### Commit E — `cost_from_usage` envelope

- `cost.cost_from_usage` callers in repo:
  - `src/localsmartz/serve.py` (one call inside `_handle_cloud_estimate`).
- The envelope add (`effective_input_tokens`) is additive. The numeric
  `estimated_usd` field changes for cache-active calls (becomes lower for
  cache_read>0, slightly higher for cache_creation>0).
- One caller; sole-consumer. ✅

## Plan-verify

Run `python3 /Users/tyroneross/.claude/plugins/cache/rosslabs-ai-toolkit/build-loop/0.10.0/scripts/plan_verify.py docs/plans/2026-05-08-groq-integration-followups.md --repo "$PWD" --json`.

## Plan-critic

Surface non-deterministic findings on alternatives, MECE scope, marker
adequacy, and headline drift after `plan-verify` returns clean.
