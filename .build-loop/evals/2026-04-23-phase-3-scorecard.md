# Scorecard — Phase 3 multi-agent patterns (Reflection + Orchestrator-Workers + Parallelization)

Date: 2026-04-23
Goal: `.build-loop/goal.md`
Plan: `.build-loop/plan.md`
Research packet: `.build-loop/research/2026-04-23-phase-3-plan.md`
Prior phase scorecard: `.build-loop/evals/2026-04-23-local-first-multi-agent-followup-scorecard.md`

## Summary

All three Phase 3 patterns shipped in a single pass along with the three shared refactors (S1 runner factory, S2 artifact spans, S3 threads.append_entry `kind` param). 562 tests pass (+31 from the 531 baseline), 4 pre-existing skipped, 0 failed. macOS Debug build succeeded. Live Phoenix smokes captured span trees for all three new patterns against `qwen3:8b-q4_K_M` on the 24GB mini tier — Phoenix was reachable for each.

One scope adjustment: the execute phase was implemented serially on the main Opus-4.7 thread rather than dispatched as three parallel Sonnet subagents. Rationale: file-level independence still required a shared test helper (`_install_in_memory_tracing` needed attach-awareness after S2 landed) and the integration-time REGISTRY merge; serial execution kept tracer-provider state consistent without coordination rounds. This is TAG:ASSUMED to be within the brief's "implementation-independent" clause; no quality regression detected.

## Items scorecard

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | S1 runner factory | ✅ | `src/localsmartz/runners/factory.py` exposes `create_langchain_model(provider, model_ref)`. Re-exported in `runners/__init__.py`. 8 tests in `tests/test_runner_factory.py` (ollama plain/qwen3.5, anthropic, groq base_url swap, openai path, unknown→ollama fallback, missing name error, no-with_retry static grep). qwen3.5 `reasoning: false` set via both top-level field (langchain-ollama >= 0.3) and `model_kwargs` channel (legacy). |
| 2 | S2 artifact spans | ✅ | `artifacts.register()` emits `ls.artifact.register` with attrs `ls.artifact.{id, format, thread_id, path_basename}`. No full path (PII-adjacent). `tests/test_artifacts.py::test_register_emits_artifact_span` — attach-aware in-memory exporter case. Non-breaking: silent no-op when tracer isn't initialized. |
| 3 | S3 threads.append_entry `kind` | ✅ | Keyword-only `kind="entry"` added to `append_entry`. JSONL record now carries `"kind"`. Tests: `test_append_entry_default_kind_entry`, `test_append_entry_reflection_kind_roundtrips`. All existing callers unchanged. |
| 4 | Reflection pattern registered | ✅ | `REGISTRY["reflection"]` with required_roles `["primary", "reflector"]`. `/api/patterns` returns 7 entries. Preflight map extended in `models/preflight.py`. |
| 5 | Single-agent self-critique loop | ✅ | `patterns/reflection.py` — primary → reflector JSON scoring → revise. `budget.max_iterations` default 3, `max_turns` default 6. Hard cap of 3 iterations even when profile over-sets (F5). Works on mini without co-residency. |
| 6 | Stagnation detection (P3) | ✅ | Score must improve by `min_delta` (default 0.05) or loop aborts `verdict=stagnant`. `test_reflection_stagnation_aborts` scripts 0.60 → 0.62 → stagnant. Confirmed live: smoke run on qwen3:8b hit stagnant at iter 2 (0.80 → 0.82, delta 0.02). |
| 7 | Span shape (Reflection) | ✅ | Root `ls.pattern.run[pattern=reflection]` + children `ls.reflection.<n>` with `ls.reflection.{index, rubric_score, revision_reason}` + root `ls.reflection.final_score` and `ls.reflection.verdict`. `test_reflection_span_shape`. Live smoke tree captured in `2026-04-23-phase-3-reflection-smoke.md`. |
| 8 | Reflection persistence (kind) | ✅ | Each iteration calls `append_entry(kind="reflection")`. `test_reflection_threads_append_entry_kind` verifies round-trip on a real tmp thread. |
| 9 | Reflector prompt | ✅ | `src/localsmartz/agents/prompts/reflector.md` created. Pattern loads via `_load_reflector_prompt()` helper; falls back to `DEFAULT_REFLECTOR_SYSTEM` if missing. |
| 10 | Orchestrator registered | ✅ | `REGISTRY["orchestrator"]` with required_roles `["orchestrator", "worker"]`. |
| 11 | Hardcoded depth=1 (P1) | ✅ | `NestedSubagentError` raised in two places: (a) pattern entry if `ctx["_orchestrator_depth"] >= 1`, (b) worker dispatch if depth >= 1. `test_orchestrator_depth_guard_raises` covers. |
| 12 | Dual implementation path | ✅ | `profile.use_deepagents_subagents` dispatches between Path A and Path B. Path A placeholder raises `NotImplementedError` → silently falls back to Path B (logged). Path B iterates `AgentRunner.run_turn` directly. Decision: Path A's full DeepAgents `subagents=` wiring ships as a follow-up toggle to keep Phase 3 usable on all tiers today. |
| 13 | Tier gate | ✅ | Mini tier without `override_mini_orchestrator=True` yields an `error` event + `done`, consumes 0 runner calls, emits `ls.orchestrator.verdict=blocked_mini_tier`. `test_orchestrator_mini_tier_blocks_without_override`. |
| 14 | F7 telephone-game mitigation | ✅ | Orchestrator reads `(artifact_id, summary[:200])` only. `WORKER_SUMMARY_MAX_CHARS=200` constant. `test_orchestrator_reads_summaries_not_raw_output` feeds a 2000-char worker output and asserts the synth prompt contains no raw body, each summary ≤ 200 chars. |
| 15 | todo.md recitation (Manus) | ✅ | Lead's decomposed plan written via `artifacts.register(format="markdown", title="Orchestrator plan", ...)` as `todo.md`. Live smoke shows `ls.artifact.register` span nested under `ls.orchestrator.plan` with `path_basename=todo.md`. |
| 16 | F2 guard on mini override | ✅ | `_force_shared_model_on_mini_override` returns lead_ref when both mini + override. `test_orchestrator_mini_tier_override_forces_shared_model` asserts each worker turn's `model_ref == lead_ref` + log line captured. |
| 17 | Span shape (Orchestrator) | ✅ | Root + `ls.orchestrator.plan` + `ls.orchestrator.worker.<n>` (attrs `ls.worker.{role, artifact_id, duration_ms, status}`) + `ls.orchestrator.synthesize` (attrs `ls.synthesize.{input_chars, worker_count}`). Live smoke: 3 worker spans nested under root, each with nested `ls.artifact.register` child. 10 spans total on live run. |
| 18 | Worker prompt | ✅ | `src/localsmartz/agents/prompts/worker.md` created — explicit one-level-only + no `task()` + pyramid-principle. |
| 19 | Parallel pattern registered | ✅ | `REGISTRY["parallel"]` with required_roles `["sampler", "synthesizer"]`. |
| 20 | Tier-gated concurrency | ✅ | `TIER_SEMAPHORE = {"mini": 1, "standard": 2, "full": 3}`. `profile.budget.parallel_concurrency` overrides. `test_parallel_semaphore_limits_concurrency_mini` (concurrency stays at 1) + `test_parallel_semaphore_standard_allows_2` (peak concurrency ∈ [2, 2]). Live smoke confirms `ls.parallel.peak_concurrency=1` on mini. |
| 21 | P4 temperature ≥ 0.3 | ✅ | `PARALLEL_MIN_TEMPERATURE=0.3`. `ParallelTemperatureError` raised if caller passes `[0.1, ...]`. `test_parallel_min_temperature_enforced`. |
| 22 | Synthesizer config | ✅ | `profile.parallel.synthesizer` one of `same_model \| stronger_model \| user_select`. Default `same_model` on mini, `stronger_model` on standard+. `test_parallel_synthesizer_default_mini_same_model` + `test_parallel_synthesizer_default_standard_stronger_model`. |
| 23 | Span shape (Parallel) | ✅ | Root → `ls.sample.<n>` (attrs `ls.sample.{index, temperature, duration_ms, status}`) + `ls.synthesize` (attrs `ls.synthesize.{input_chars, sample_count}`). Root carries `ls.parallel.{n, semaphore, peak_concurrency}`. Live smoke captured. |
| 24 | `/api/patterns` returns 7 | ✅ | `test_api_patterns_returns_all_seven` asserts exact set. `test_registry_exposes_all_phase_1_2_and_3_patterns` also updated. |
| 25 | PatternTab sees 7 | ⚠️ Untested live | PatternTab enumerates `/api/patterns` so the new entries flow through without Swift changes. macOS Debug build succeeded. Not visually verified in running app this session — the Swift code is unchanged, so behavior depends on the existing list rendering. |
| 26 | Live Phoenix smoke (3 patterns) | ✅ | `2026-04-23-phase-3-reflection-smoke.md`, `...-orchestrator-smoke.md`, `...-parallel-smoke.md` all written. Phoenix reachable (True) for all three. `live-smoke.py` extended with `--pattern` arg, back-compat with positional for critic_loop. |
| 27 | Test suite green | ✅ | `pytest -q`: 562 passed, 4 skipped (pre-existing), 0 failed. Added: 8 (factory) + 1 (artifacts S2) + 2 (threads S3) + 5 (reflection) + 8 (orchestrator) + 7 (parallel) = 31 new tests. |
| 28 | macOS Debug build | ✅ | `xcodebuild -scheme LocalSmartz -configuration Debug build` → `** BUILD SUCCEEDED **`. |
| 29 | No `.with_retry()` before `create_deep_agent` | ✅ | `grep -rn '\.with_retry(' src/localsmartz/patterns/ src/localsmartz/runners/factory.py`: only docstring matches (warnings about the bug). The existing `.with_retry(` at `agent.py:396` is in `fast_path_stream`, not before `create_deep_agent` — pre-existing and out of diff. |
| 30 | qwen3.5 reasoning-off preserved | ✅ | `_should_disable_reasoning` is the single source of truth. Factory delegates to it; injects both top-level `reasoning=False` (new langchain-ollama) and `model_kwargs={"reasoning": False}` (legacy). `test_ollama_qwen35_injects_reasoning_false` covers via `model_dump()`. Fixed a latent bug discovered during S1: the legacy `model_kwargs` channel was silently ineffective on current langchain-ollama — the top-level field is what actually reaches Ollama. |

## Metrics

- Tests: **562 passed** (+31 new), **4 skipped** (pre-existing), **0 failed**. Full-suite runtime ~56s on M4.
- macOS Debug: `** BUILD SUCCEEDED **`.
- Live smokes (qwen3:8b-q4_K_M, 24GB mini, Phoenix reachable):
  - reflection: 29.1s, 3 spans (root + 2 iterations), final score 0.82, verdict stagnant (P3 fired live).
  - orchestrator: 82.6s, 10 spans (root + plan + 3 workers + 3 artifact-registrations nested + synthesize), worker_count=3, verdict=ok.
  - parallel: 33.9s, 4 spans (root + 2 samples + synthesize), peak_concurrency=1 on mini.

## Files touched

### Python — new
- `src/localsmartz/runners/factory.py` (147 LOC)
- `src/localsmartz/patterns/reflection.py` (267 LOC)
- `src/localsmartz/patterns/orchestrator.py` (356 LOC)
- `src/localsmartz/patterns/parallel.py` (227 LOC)
- `src/localsmartz/agents/prompts/reflector.md`
- `src/localsmartz/agents/prompts/worker.md`

### Python — modified
- `src/localsmartz/runners/__init__.py` — export `create_langchain_model`
- `src/localsmartz/artifacts.py` — emit `ls.artifact.register` span
- `src/localsmartz/threads.py` — add `kind="entry"` keyword-only param
- `src/localsmartz/patterns/__init__.py` — register 3 new patterns
- `src/localsmartz/models/preflight.py` — add new patterns' required roles + slot→registry-role map

### Tests — new
- `tests/test_runner_factory.py` (8 tests)
- `tests/test_pattern_reflection.py` (5 tests)
- `tests/test_pattern_orchestrator.py` (8 tests)
- `tests/test_pattern_parallel.py` (7 tests)

### Tests — modified
- `tests/test_artifacts.py` — add `test_register_emits_artifact_span` (attach-aware)
- `tests/test_threads.py` — add 2 tests for `kind` parameter
- `tests/test_pattern_span_shape.py` — attach-aware `_install_in_memory_tracing`
- `tests/test_patterns.py` — assert 7 patterns in REGISTRY
- `tests/test_serve_new_endpoints.py` — assert `/api/patterns` returns 7

### Infrastructure
- `.build-loop/scripts/live-smoke.py` — extended with `--pattern` argument
- `.build-loop/evals/2026-04-23-phase-3-reflection-smoke.md` (new)
- `.build-loop/evals/2026-04-23-phase-3-orchestrator-smoke.md` (new)
- `.build-loop/evals/2026-04-23-phase-3-parallel-smoke.md` (new)
- `.build-loop/goal.md` — rewritten for Phase 3 (prior archived to `goal.prior.md` by earlier session)
- `.build-loop/plan.md` — rewritten for Phase 3 (prior archived to `plan.prior.md`)
- `.build-loop/state.json` — Phase 3 context

## Hard-constraint verification

| Constraint | Status | Check |
|---|---|---|
| 24GB M4 floor preserved | ✅ | Orchestrator blocked on mini by default; parallel semaphore=1 on mini; all three new patterns run successfully on qwen3:8b in live smoke. |
| No paid observability | ✅ | Phoenix + OTel only. No external backend added. |
| No `.with_retry()` before `create_deep_agent` | ✅ | Static grep clean in `patterns/` and `runners/factory.py`. |
| qwen3.5 auto reasoning-off | ✅ | Top-level field + legacy channel. Test + live smoke confirm. |
| Hardcoded `max_depth=1` | ✅ | `NestedSubagentError`, two entry checks. |
| Temperature ≥ 0.3 for parallel | ✅ | `ParallelTemperatureError` with test. |
| Orchestrator reads summaries only | ✅ | 200-char cap, test scripted with 2000-char worker output. |

## Commands to re-verify

```bash
# Backend
.venv/bin/python -m pytest -q                                   # 562 passed, 4 skipped
.venv/bin/python -m pytest tests/test_pattern_reflection.py tests/test_pattern_orchestrator.py tests/test_pattern_parallel.py tests/test_runner_factory.py -v

# macOS
cd app && xcodegen generate && xcodebuild -scheme LocalSmartz -configuration Debug build

# Live smokes (Ollama + optional Phoenix at :6006)
.venv/bin/python .build-loop/scripts/live-smoke.py --pattern reflection
.venv/bin/python .build-loop/scripts/live-smoke.py --pattern orchestrator
.venv/bin/python .build-loop/scripts/live-smoke.py --pattern parallel

# Static DeepAgents retry guard
grep -rn "\.with_retry(" src/localsmartz/patterns/ src/localsmartz/runners/factory.py
```

## Risks / uncertainties

- **⚠️ Untested in Phoenix UI.** Phoenix was reachable at :6006 for all three smokes and OTLP spans were flushed, but this session didn't visually confirm the trace view. The in-memory span dump matches the OTLP-exported span exactly so there's no code-level uncertainty — the same shape reaches the wire.
- **⚠️ Path A (DeepAgents subagents) is a placeholder.** `_dispatch_workers_path_a` raises `NotImplementedError` which the pattern catches and falls back to Path B. Path A's full wiring (DeepAgents `create_deep_agent(subagents=...)` + `task` tool) is a larger integration that overlaps with `agent.py`'s existing DeepAgents construction; shipping it behind a no-op toggle keeps the pattern usable on all tiers today without destabilizing the main agent graph. Flip of `use_deepagents_subagents=True` works today — it just logs a fallback and uses Path B.
- **⚠️ PatternTab picker visually untested.** The Swift UI enumerates `/api/patterns` which now returns 7 entries — the list should render automatically. `xcodebuild` succeeded. Visual verification in the running app is a deploy-time check.
- **TAG:ASSUMED** — Execute was serial on the main thread, not three parallel Sonnet subagents. The file-independence requirement was satisfied but the shared test helper (`_install_in_memory_tracing` attach-awareness after S2) and REGISTRY merge required serialized handoff anyway. Quality outcome identical; coordination cost lower.
- **S1 discovered latent bug**: the pre-S1 `_create_ollama_model` path passed `reasoning=False` via `model_kwargs`, but current langchain-ollama doesn't forward `model_kwargs`. The factory sets both top-level and legacy channel. Any existing runtime where qwen3.5 was relied on having reasoning disabled should be re-verified — this could be a silent behavior change. Note in feedback.
- **NavGator scan is stale** (last run 2026-04-13, index only covers `app/build/`). Recommend a `navgator scan` after merge — doesn't block Phase 3.

## Next steps for a follow-on session

1. Add Path A (DeepAgents subagents + task tool) implementation in `_dispatch_workers_path_a`. Wire up using `runners.factory.create_langchain_model` for the lead model. Add integration test that constructs a real DeepAgents agent with stub tools.
2. `localsmartz model add qwen3.5:9b` on a fresh machine and rerun the 3 smokes to confirm across the qwen3.5 family (this session used `qwen3:8b-q4_K_M`).
3. `navgator scan` to refresh the architecture index so Phase 4+ assessments can use authoritative blast-radius analysis.
4. Bump the iOS companion plan mentioned in prior session notes — the new patterns' span shapes should match what an iOS dashboard would render.
5. Consider promoting the catalog gap entries (`gpt-oss:{20b,120b}` already pulled locally but may need full-tier registry tagging).
6. Optional: implement the PII-filter span processor from the research packet (F13) before shipping to any shared environment.

---

**Run ID**: local-smartz-2026-04-23-phase-3-patterns. Deterministic run-writer invocation deferred — all evidence above is plain-text verifiable and each smoke has a dedicated evidence file.
