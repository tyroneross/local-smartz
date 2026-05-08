# Plan — LLM Provider Optimizations (2026-05-08)

**Run-id:** `run_20260508T082242Z_c5909ea4`
**Branch:** `main` (no feature branch — single-orchestrator, sequential commits)
**Goal:** `.build-loop/goal.md`
**Intent:** `.build-loop/intent.md`
**Baseline:** `git log -1 --oneline` HEAD; `uv run pytest` 674 collected.
**Pre-commit hooks:** none beyond pytest invocation in CI.

## North Star Reminder
Local-Ollama stays default. Cloud (Anthropic, OpenAI, Groq) is first-class fallback. Groq is the user's preferred cloud fallback. No new dependencies. Ollama runner untouched. `_create_model` (DeepAgents path) and `create_langchain_model` (patterns path) stay separate — RunnableRetry is unhashable inside DeepAgents (`reference_deepagents_runnable_retry.md`).

## Constraints
- Tests must pass at every commit. New tests increase the count; never decrease.
- No API keys are present in this environment. Cloud-touching tests must skip gracefully via `pytest.importorskip` patterns or env-key guards.
- Implementers do NOT commit; orchestrator commits sequentially after each batch.
- Iteration cap 5 per commit; remaining commits surface as ❓ Unfixed if cap hit.

## Dependency Graph

```
c1  (eval scorecard) ─────────── gates R2..R4 (empirical baseline)
                                    │
       ┌──────────┬──────────┬──────┴────┬──────────┐
       c2         c3         c4          c5         (R2 — parallel-safe; disjoint files)
   anthropic   real-tokens retry/backoff  parallel-workers
       │          │          │              │
       └──────────┴──────────┴──────────────┘
                                    │
       ┌──────────┬──────────┬──────┴────┐
       c6         c7         c8         c9    (R3 — parallel-safe; disjoint files)
   streaming   tier-routing  budget-warn  pricing-freshness
                                    │
                                  c10           (R4 — bundle: error classes + OTel parity + dual-path doc)
```

Within each batch, commits are MECE on files (no overlaps). Commits land sequentially due to the single-writer git contract; parallel **work** + sequential **commit**.

## Synthesis Dimensions Routing

Each commit declares `synthesis_dimensions:` with explicit phrasing per dim. Counts at commit level: c1=3, c2=4, c3=2, c4=3, c5=3, c6=3, c7=4, c8=3, c9=2, c10=3. None exceed 5 → default Sonnet fan-out per build-loop §"Synthesis-density routing"; C3 attestation_lint, C4 synthesis-critic (UI commits only — n/a here, all backend), C5 halt-and-ask backstops apply.

## Commit Table

### c1 — Multi-provider eval scorecard (R1, gates everything)
- **commit_subject:** `feat(model_eval): multi-provider parity scorecard with key-skip`
- **files_owned:**
  - `src/localsmartz/model_eval.py` (extend)
  - `tests/test_model_eval_multi_provider.py` (new)
  - `.build-loop/evals/2026-05-08-multi-provider-parity.tsv` (generated, committed)
  - `.build-loop/evals/2026-05-08-multi-provider-parity.md` (generated, committed)
- **modifies_api:** `model_eval.run_model_matrix`, `model_eval.matrix_to_dict`, new `model_eval.run_multi_provider`, new CLI flag `--multi-provider`.
- **f_criteria:**
  - F1: `uv run python -m localsmartz.model_eval --multi-provider --json` exits 0
  - F2: TSV + markdown written to `.build-loop/evals/2026-05-08-multi-provider-parity.{tsv,md}`
  - F3: Cloud providers without API keys log a single `[skip] {provider}: no API key` line and don't appear in scoreboard
  - F4: Ollama provider produces ≥1 model row with `weighted_score > 0` (assumes a local model is reachable; if Ollama daemon is offline, the test logs a skip and exits 0 — `xfail`-style)
  - F5: New test file passes (skips cleanly when Ollama unreachable)
- **synthesis_dimensions:**
  - `output_shape: TSV with header [provider, model, task, ok, latency_ms, weight, category]; markdown with one table per provider grouped by model`
  - `key_skip_policy: missing key emits one stderr line per provider, NOT a Python traceback; provider absent from final scoreboard`
  - `cli_flag: --multi-provider triggers Ollama + (anthropic if key) + (openai if key) + (groq if key); default behavior unchanged`
- **verifications:** `uv run pytest tests/test_model_eval_multi_provider.py -x`; manual `uv run python -m localsmartz.model_eval --multi-provider --json | head -30`.
- **synthesis_attestation_required:** yes (each dim graded post-commit by attestation_lint where deterministic).

### c2 — Anthropic prompt caching
- **commit_subject:** `feat(runners): anthropic prompt caching on system + tools`
- **files_owned:**
  - `src/localsmartz/runners/cloud_anthropic.py`
  - `tests/test_runner_anthropic_caching.py` (new)
- **modifies_api:** `CloudAnthropicRunner.run_turn` — adds `cache_control` blocks; `AssistantTurn.usage` gains `cache_creation_input_tokens` + `cache_read_input_tokens` (Usage TypedDict in `runners/base.py` extended via `total=False`).
- **also_touches:** `src/localsmartz/runners/base.py` (Usage TypedDict gains 2 optional fields). This is the ONLY exception to MECE — but base.py changes are additive (TypedDict optional fields), so c3 sees them as no-ops.
- **f_criteria:**
  - F1: `cache_control: {type: "ephemeral"}` is set on the system block (when system is present) AND on the LAST tool definition (Anthropic's "cache the tools array" idiom)
  - F2: Returned `AssistantTurn["usage"]` includes `cache_creation_input_tokens` + `cache_read_input_tokens` keys when SDK returns them; absent when SDK doesn't
  - F3: Test mocks the SDK response (no API key needed), asserts request payload contains `cache_control` blocks, asserts response usage carries cache fields
  - F4: `pytest -x` green
- **synthesis_dimensions:**
  - `cache_target: system block (when present) + final tool entry; not the user message (per Anthropic 2026 guidance — system+tools have the long-lived prefixes)`
  - `cache_block_form: {"type": "ephemeral"} appended to the existing block dict (system becomes [{"type":"text","text":sys,"cache_control":{...}}], tools[-1]["cache_control"] = {...})`
  - `usage_field_propagation: pass through verbatim from SDK; do NOT compute or estimate; absent when SDK absent`
  - `backwards_compat: existing AssistantTurn consumers see ZERO breaking changes (cache fields are extra optional keys on Usage TypedDict)`
- **verifications:** `uv run pytest tests/test_runner_anthropic_caching.py tests/test_runner_no_key_graceful.py -x`; py_compile.
- **synthesis_attestation_required:** yes.

### c3 — Real token counts from SDK + cost.py prefer-real
- **commit_subject:** `feat(cost): prefer SDK token counts over estimate`
- **files_owned:**
  - `src/localsmartz/cost.py`
  - `tests/test_cost_prefer_real.py` (new)
- **modifies_api:** new `cost.cost_from_usage(model, usage_dict)` that takes a real `Usage` dict and returns the same envelope as `estimate_cost_usd` but with `rate_known: True` + `source: "sdk"` when usage is non-empty, else falls back to `estimate_cost_usd` with `source: "estimate"`. Existing `estimate_cost_usd` unchanged.
- **f_criteria:**
  - F1: New `cost_from_usage` returns `source: "sdk"` when `usage` is non-empty, mirrors estimate result with `source: "estimate"` when empty
  - F2: Cache-creation/read tokens, when present in `usage`, count toward total (cache reads charged at the discounted rate per Anthropic — but for now, cache fields are surfaced verbatim in the envelope; we don't apply the discount math in this commit, just expose the numbers and document the deferred math)
  - F3: `estimate_cost_usd` semantics unchanged (proven by re-running existing tests if any; no test regression)
  - F4: New test asserts both branches
- **synthesis_dimensions:**
  - `function_split: keep estimate_cost_usd intact; add cost_from_usage as the SDK-real path; both share rate lookup`
  - `cache_token_handling: surface cache_creation/read in the envelope; defer discount math to a future commit (note in docstring + .build-loop/issues/cache-discount-math.md)`
- **verifications:** pytest green; py_compile.

### c4 — Retry/backoff on cloud runners
- **commit_subject:** `feat(runners): exponential-backoff retry on cloud SDK calls`
- **files_owned:**
  - `src/localsmartz/runners/cloud_anthropic.py` (already touched in c2; **conflict** — see note below)
  - `src/localsmartz/runners/cloud_openai_compat.py`
  - `src/localsmartz/runners/_retry.py` (new helper)
  - `tests/test_runner_retry.py` (new)
- **MECE conflict resolution:** c2 and c4 both edit `cloud_anthropic.py`. Land c2 first; c4's edit happens AFTER c2 has been committed, so the diff is layered cleanly. No parallel implementation — c4 implementer reads HEAD post-c2.
- **modifies_api:** `_retry.with_retry(coroutine, *, max_attempts=3, base_delay=1.0, max_delay=20.0)` async wrapper; cloud runners' `run_turn` wraps the SDK call in `with_retry`.
- **f_criteria:**
  - F1: Transient errors (`httpx.TransportError`, `httpx.TimeoutException`, 429 with Retry-After) retry with exponential backoff up to 3 attempts; auth/4xx (except 429), 400 context-overflow → fail loud, no retry
  - F2: 429 honors `Retry-After` header when present (else exponential); auth errors raise immediately
  - F3: Max wait clamp at 20s per attempt
  - F4: Test mocks transient → success after 2 retries; mocks auth → no retry
  - F5: NOT `RunnableRetry` — direct stdlib `asyncio.sleep` + try/except. The deepagents memory citation goes in `_retry.py` module docstring.
- **synthesis_dimensions:**
  - `retry_layer: SDK call only (the await client.X.create(...) line); not the runner method's outer try/except`
  - `transient_classification: [httpx.TransportError, httpx.TimeoutException, 429]; everything else fails immediately`
  - `no_RunnableRetry: stdlib only; cite reference_deepagents_runnable_retry.md in module docstring`
- **verifications:** pytest green; py_compile.

### c5 — Parallel worker dispatch
- **commit_subject:** `perf(orchestrator): parallel worker dispatch in Path B (asyncio.gather)`
- **files_owned:**
  - `src/localsmartz/patterns/orchestrator.py` (only the Path B `_dispatch_workers_path_b` function)
  - `tests/test_orchestrator_parallel.py` (new)
- **modifies_api:** internal — `_dispatch_workers_path_b` no longer iterates serially; uses `asyncio.gather` with a per-provider `asyncio.Semaphore`. Signature unchanged. Result-list ordering preserved.
- **f_criteria:**
  - F1: `_dispatch_workers_path_b` returns the same shape/order as before for N workers
  - F2: Concurrency cap: cloud providers (anthropic/openai/groq) ≤4 in flight simultaneously per provider; Ollama unbounded (local has its own queue)
  - F3: One worker raising NestedSubagentError still fails the batch (semantics preserved)
  - F4: Test creates 4 fake workers with stubbed runner that records start/end timestamps; asserts overlap
- **synthesis_dimensions:**
  - `concurrency_primitive: asyncio.gather(...) wrapped per task in a semaphore.acquire() context; not a queue, not a thread pool`
  - `cap_table: cloud=4 per provider; ollama=unlimited; cap key derived from worker_agent.model_ref["provider"]`
  - `result_ordering: preserved by indexing — gather returns in argument order, append in same order; no sort needed`
- **verifications:** pytest green; py_compile.

### c6 — Token-level streaming on cloud runners
- **commit_subject:** `feat(runners): token-level streaming on cloud runners`
- **files_owned:**
  - `src/localsmartz/runners/cloud_anthropic.py` (3rd touch; layered on c2+c4)
  - `src/localsmartz/runners/cloud_openai_compat.py` (2nd touch; layered on c4)
  - `src/localsmartz/runners/base.py` (extend Protocol with optional `stream_turn`)
  - `tests/test_runner_streaming.py` (new)
- **modifies_api:** new optional async-iterator method `stream_turn(...)` on the AgentRunner Protocol; concrete runners implement it. `run_turn` unchanged. Bridge to `serve.py` SSE deferred to a follow-up — this commit ships the runner-side primitive only and a stub demonstrating SSE flush works.
- **f_criteria:**
  - F1: `stream_turn` yields incremental `{delta: str, done: bool}` dicts
  - F2: Anthropic uses `client.messages.stream()`; OpenAI/Groq uses `stream=True` + chunk iteration
  - F3: Final yielded dict has `done: True` and includes the full `AssistantTurn` envelope under `final`
  - F4: Test mocks SDK stream and asserts deltas concatenate to the full content
- **synthesis_dimensions:**
  - `stream_payload_shape: {"delta": str, "done": bool, "final"?: AssistantTurn} — keep the surface tiny so SSE bridging is straightforward`
  - `final_event_required: every stream MUST end with done=True even on error; error path yields {"delta": "", "done": True, "error": str}`
  - `serve_bridge_deferred: serve.py SSE wiring is a separate commit (out of scope for this batch); this commit only ships the runner primitive`
- **verifications:** pytest green; py_compile.

### c7 — Tier-aware task routing
- **commit_subject:** `feat(profiles): tier-aware role→model mapping with cloud fallback`
- **files_owned:**
  - `src/localsmartz/profiles.py`
  - `tests/test_profiles_tier_routing.py` (new)
- **modifies_api:** new `TIER_BY_ROLE = {planner: "mid", researcher: "cheap", analyzer: "cheap", writer: "strong", fact_checker: "mid", orchestrator: "mid"}`; new `CLOUD_TIER_TABLE = {anthropic: {cheap: "claude-haiku-4", mid: "claude-sonnet-4-6", strong: "claude-opus-4-7"}, groq: {cheap: "llama-3.3-70b-versatile", mid: "llama-3.3-70b-versatile", strong: "llama-3.3-70b-versatile"}, openai: {cheap: "gpt-4o-mini", mid: "gpt-4o-mini", strong: "gpt-4o"}}`; new `resolve_model_for_role(role, provider) -> str`. Existing AGENT_ROLES dict unchanged.
- **f_criteria:**
  - F1: `resolve_model_for_role("writer", "anthropic")` → "claude-opus-4-7"
  - F2: `resolve_model_for_role("researcher", "groq")` → "llama-3.3-70b-versatile"
  - F3: Unknown role defaults to tier "mid"; unknown provider raises ValueError with provider name
  - F4: Existing role-prompt loading path unchanged (no AGENT_ROLES regression)
- **synthesis_dimensions:**
  - `tier_table_shape: nested dict {provider: {tier: model_name}}; static module-level constant; no env-var override in this commit`
  - `role_to_tier: planner=mid, researcher=cheap, analyzer=cheap, writer=strong, fact_checker=mid, orchestrator=mid (per user brief — fast_path mapped to researcher; classifier→analyzer; synthesizer→writer)`
  - `groq_default_per_brief: Groq cheap+mid+strong all map to llama-3.3-70b-versatile (single Groq model in cost.RATES); overrideable in a future commit`
  - `unknown_provider: raise ValueError, do NOT silently fall back — caller must handle`
- **verifications:** pytest green; py_compile.

### c8 — Cloud-only token budget warn
- **commit_subject:** `feat(serve): advisory token-budget SSE event on cloud paths`
- **files_owned:**
  - `src/localsmartz/serve.py` (extension only — new event emit point + config read)
  - `tests/test_serve_budget_warn.py` (new — unit-tests the helper, not the full HTTP path)
  - `.localsmartz/config.example.json` (new — documents the new key without changing live config)
- **modifies_api:** new `serve._emit_budget_warn(session_tokens, threshold) -> dict` helper that returns the SSE event dict; new config key `budget_tokens_warn` (default `null` = disabled). When threshold crossed during a cloud run, SSE emits `event: budget_warn\ndata: {"session_tokens": N, "threshold": M, "advisory": true}`.
- **f_criteria:**
  - F1: Helper returns proper SSE-formatted event dict
  - F2: `null` threshold = no event emitted (disabled)
  - F3: Ollama runs do NOT trigger the helper (cloud-only — provider check)
  - F4: NO abort behavior — strictly advisory; existing run continues
  - F5: Test asserts helper output shape; full serve.py integration test would require a session — defer
- **synthesis_dimensions:**
  - `advisory_only_no_abort: emit event, log warning, continue; explicitly NOT a hard cutoff`
  - `cloud_only_filter: provider == "ollama" → no event regardless of token count`
  - `config_default_null: backwards-compat — existing configs without the key behave exactly as before`
- **verifications:** pytest green; py_compile.

### c9 — Pricing freshness signal
- **commit_subject:** `feat(cost): pricing-freshness signal + refresh script stub`
- **files_owned:**
  - `src/localsmartz/cost.py` (2nd touch; layered on c3)
  - `scripts/refresh-pricing.py` (new — stub with provider-pricing URLs)
  - `tests/test_cost_freshness.py` (new)
- **MECE conflict resolution:** c3 and c9 both edit `cost.py`. c9 lands AFTER c3; c9 implementer reads HEAD post-c3.
- **modifies_api:** new `cost.LAST_VERIFIED = LAST_UPDATED` (alias, deprecate `LAST_UPDATED` in a future commit); new `cost.pricing_freshness() -> {"last_verified": ISO, "age_days": N, "stale": bool}`; threshold = 30 days. `rate_age_days()` already exists — wraps it.
- **f_criteria:**
  - F1: `pricing_freshness()` returns `stale: True` when `age_days > 30`
  - F2: `scripts/refresh-pricing.py --check` prints the freshness envelope and exits 0 always (informational)
  - F3: `scripts/refresh-pricing.py` lists provider pricing URLs in its docstring (anthropic.com/pricing, openai.com/api/pricing, groq.com/pricing) — does NOT scrape (per brief)
  - F4: Test asserts threshold logic
- **synthesis_dimensions:**
  - `no_scraping: refresh script is documentation, not an automation — provider URLs in docstring only`
  - `threshold_30_days: matches user brief; stored as a module constant for tweakability`
- **verifications:** pytest green; py_compile.

### c10 — Tier 3 hygiene bundle
- **commit_subject:** `chore(runners): error taxonomy + OTel parity + dual-path doc`
- **files_owned:**
  - `src/localsmartz/runners/base.py` (3rd touch — adds error classes)
  - `src/localsmartz/runners/cloud_anthropic.py` (4th touch — error mapping)
  - `src/localsmartz/runners/cloud_openai_compat.py` (3rd touch — error mapping)
  - `src/localsmartz/runners/factory.py` (1st touch — comment block on dual-path)
  - `src/localsmartz/agent.py` (1st touch — same comment block referencing factory.py + memory)
  - `src/localsmartz/observability.py` (1st touch — verify cloud spans exist; add if missing)
  - `tests/test_runner_error_taxonomy.py` (new)
- **MECE conflict resolution:** c10 is the LAST commit; all prior cloud_anthropic/openai_compat edits already in HEAD. Layered cleanly.
- **modifies_api:** new error classes in `runners/base.py`: `RunnerError` (base), `RunnerRateLimit`, `RunnerAuth`, `RunnerTransient`, `RunnerContextOverflow`, `RunnerUnknown` — all subclasses of `RunnerError`. Cloud runners catch SDK exceptions and re-raise as the appropriate class. Comment blocks added to `factory.py` and `agent.py::_create_model` citing each other AND the deepagents memory.
- **f_criteria:**
  - F1: New error classes exported from `runners/base.py`; available via `from localsmartz.runners.base import RunnerRateLimit` etc.
  - F2: `cloud_anthropic.run_turn` and `cloud_openai_compat.run_turn` catch known SDK exceptions and re-raise as Runner* classes
  - F3: `c4`'s retry helper updated to catch `RunnerTransient` + `RunnerRateLimit` (not raw httpx) — completes the abstraction
  - F4: `factory.py` and `agent.py` carry IDENTICAL comment blocks pointing at each other + `~/.claude/projects/-Users-tyroneross/memory/reference_deepagents_runnable_retry.md`
  - F5: `observability.py` has at least one `start_as_current_span` per cloud provider's `run_turn` (audit pass — add a missing span if any)
  - F6: Tests assert each new error class is raised on the right SDK error
- **synthesis_dimensions:**
  - `error_class_taxonomy: 5 specific subclasses of RunnerError; named for what the caller does (retry/auth-fix/abort), not for the SDK that emitted them`
  - `dual_path_doc: comment block in BOTH files referencing each other + the memory file; do NOT collapse the implementations`
  - `otel_span_audit: verify by grep first; add only if missing — no over-instrumentation`
- **verifications:** pytest green; py_compile; manual `grep -r "RunnableRetry" src/localsmartz/ | grep -v "_retry.py docstring"` returns empty (proves we didn't reintroduce the bad pattern).

## File Ownership Matrix (MECE proof)

| File | Owner | Touch count | Notes |
|---|---|---|---|
| `src/localsmartz/model_eval.py` | c1 | 1 | |
| `src/localsmartz/runners/base.py` | c2 (Usage extend), c6 (Protocol extend), c10 (errors) | 3 | All additive; no rewrites |
| `src/localsmartz/runners/cloud_anthropic.py` | c2 (caching), c4 (retry), c6 (stream), c10 (errors) | 4 | Sequential layering required |
| `src/localsmartz/runners/cloud_openai_compat.py` | c4 (retry), c6 (stream), c10 (errors) | 3 | Sequential layering |
| `src/localsmartz/runners/_retry.py` | c4 (new) | 1 | |
| `src/localsmartz/runners/factory.py` | c10 (doc only) | 1 | |
| `src/localsmartz/cost.py` | c3, c9 | 2 | Sequential |
| `src/localsmartz/patterns/orchestrator.py` | c5 (Path B only) | 1 | Path A untouched |
| `src/localsmartz/profiles.py` | c7 | 1 | AGENT_ROLES untouched |
| `src/localsmartz/serve.py` | c8 (helper only) | 1 | Existing SSE machinery untouched |
| `src/localsmartz/agent.py` | c10 (doc only) | 1 | DeepAgents path untouched |
| `src/localsmartz/observability.py` | c10 (audit + add-if-missing) | 1 | |
| `scripts/refresh-pricing.py` | c9 (new) | 1 | |
| `.localsmartz/config.example.json` | c8 (new) | 1 | |
| `tests/test_*` | per-commit | per-commit | One new test file per commit |
| `.build-loop/evals/2026-05-08-multi-provider-parity.{tsv,md}` | c1 (generated) | 1 | |

**Verdict:** every file has exactly one logical owner per commit; multi-touch files (`cloud_anthropic.py`, `cost.py`, `runners/base.py`) are sequential additive layers. No two commits write the same lines.

## Caller Audit (Scope Auditor section)

Modifies-API summary per commit; manually traced because scope-auditor agent dispatch isn't part of this top-level orchestration (single-orchestrator mode — orchestrator IS the auditor).

- **c1 — `model_eval.run_model_matrix` extension:** callers = CLI `main()` and tests. CLI `main()` is in same file (c1 owns); tests are c1's new file. **In-scope.**
- **c2 — `Usage` TypedDict gains 2 optional fields + `CloudAnthropicRunner.run_turn` extends payload:** TypedDict extension is backwards-compat (TypedDict allows extra optional keys with `total=False` already in place). `run_turn` callers: `agent.py`, `patterns/*.py`, `model_eval.py` — none read `cache_creation_input_tokens` etc., so extension is invisible to them. **In-scope.**
- **c3 — `cost.cost_from_usage` new function:** callers = none yet (additive). Future commits/UI may consume; not this batch. **In-scope.**
- **c4 — `_retry.with_retry` new helper + cloud runners' SDK call wrapped:** runners' external surface (`run_turn` signature/return) unchanged. `with_retry` is internal. **In-scope.**
- **c5 — `_dispatch_workers_path_b` internal change:** signature/return unchanged; only execution shape. Callers of `_dispatch_workers_path_b` (orchestrator main) see same outputs. **In-scope.**
- **c6 — `AgentRunner.stream_turn` Protocol method:** Protocol extension is non-breaking when method is optional (`Protocol` runtime check accepts implementations missing the method as long as it's optional or marked with `@runtime_checkable` cautiously). Confirmed: `AgentRunner` is `Protocol` not `runtime_checkable` — additive. **In-scope.**
- **c7 — `resolve_model_for_role` new function:** additive. Callers will adopt later. **In-scope.**
- **c8 — `serve._emit_budget_warn` new helper:** internal to serve.py; no external caller. **In-scope.**
- **c9 — `cost.pricing_freshness` new function + `cost.LAST_VERIFIED` alias:** additive. **In-scope.**
- **c10 — Runner* error classes new:** callers must catch new types if they want fine-grained handling, but `RunnerError` base means existing broad `except Exception:` paths still work. Default behavior preserved. **In-scope.**

**Verdict:** `scope_gap_found: false` — no out-of-scope caller-site updates required.

## Test Strategy

Each commit adds 1 test file (named in `files_owned`). Tests use:
- `pytest.importorskip("anthropic")` / `("openai")` for SDK-presence skips
- `pytest.fixture(autouse=False)` mocks for SDK clients (no real API calls)
- `monkeypatch.setenv` to control key presence per test
- `pytest.skip("Ollama daemon unreachable")` when c1's full-matrix test needs a live local model

Baseline: 674 collected. Target: ≥684 collected (10 commits, ≥1 new test each).

## Run Plan

1. Sequential commit landing: c1 → c2 → c3 → c4 → c5 → c6 → c7 → c8 → c9 → c10.
2. Each commit: implementer reads HEAD, makes minimal change, writes test, returns envelope; orchestrator commits, runs `uv run pytest -x`, advances to next.
3. On any commit failing tests: route to Iterate (cap 5); if cap hit, surface as ❓ Unfixed and proceed to remaining commits flagged as `depends_on: <failed>` only if dependency is logical not technical (most c2..c9 don't strictly depend on c1's test passing — only c1's CLI extension is needed for c4 to pass scorecard manually).
4. Final Review-F: scorecard + commit hash list.

## plan-verify expectations
- All `f_criteria` are concrete (commands or assertions, no vague verbs)
- All `synthesis_dimensions` have explicit phrasing
- `files_owned` is enumerated per commit
- `modifies_api` declared per commit
- LoC budget: c1 ≤300 net, c2 ≤120 net, c3 ≤80 net, c4 ≤180 net, c5 ≤120 net, c6 ≤220 net, c7 ≤120 net, c8 ≤80 net, c9 ≤80 net, c10 ≤200 net

## plan-critic expectations
WARN-only items expected:
- "MECE conflict on cloud_anthropic.py" — addressed via sequential commit ordering (documented above)
- "c10 doc-only commits to factory.py + agent.py" — justified by user constraint (do NOT collapse dual-path)
- "c1 baseline depends on Ollama daemon being live" — addressed via skip-on-unreachable in test
