# Plan — Phase 3 patterns (Reflection + Orchestrator-Workers + Parallelization)

Goal: `.build-loop/goal.md`
Research packet: `.build-loop/research/2026-04-23-phase-3-plan.md`
Prior plan archived: `.build-loop/plan.prior.md`

## Sequencing

**Chunk 1 (main thread, sequential)** — shared refactors S1, S2, S3. Must land green before chunks 2A/2B/2C are dispatched. Each refactor is small (<30 min) and lays the substrate three patterns rely on.

**Chunk 2A, 2B, 2C (parallel subagents)** — Pattern A (Reflection), Pattern B (Orchestrator-Workers), Pattern C (Parallelization). Dispatched as three concurrent Sonnet implementers. Each pattern is file-independent (their own `patterns/<name>.py`) but all three register into `REGISTRY` which the main orchestrator edits at merge time.

**Chunk 3 (main thread)** — integration: update `REGISTRY`, run full pytest, macOS build, live Phoenix smoke for each pattern, Swift picker verification via `/api/patterns`.

**Chunk 4 (Review)** — standard build-loop review sequence.

## Chunk 1 — shared refactors (main thread)

### S1 — `runners/factory.py` extract

- Create `src/localsmartz/runners/factory.py` with `create_langchain_model(provider: str, model_ref: dict) -> Any`.
- Move the bodies of `_create_ollama_model`, `_create_anthropic_model`, `_create_openai_compat_model` from `agent.py` into the factory module (deduplicate; preserve qwen3.5 `reasoning: false` injection path).
- `agent.py::_create_model` becomes a thin wrapper that resolves provider via `_active_provider()` + role via `get_model()` and calls the factory.
- Export `create_langchain_model` in `runners/__init__.py`.
- Test: `tests/test_runner_factory.py` — 4 cases (ollama plain, ollama qwen3.5 with reasoning=False asserted, anthropic lazy-import, openai base_url swap for groq).
- Grep guard: confirm no `.with_retry(` in the factory, preserved via existing test.

### S2 — `artifacts.register` emits `ls.artifact.register` span

- In `artifacts.register()`, open a span named `ls.artifact.register` via `localsmartz.observability.get_tracer("localsmartz.artifacts")`. Attrs: `ls.artifact.id`, `ls.artifact.format`, `ls.artifact.thread_id`, `ls.artifact.path_basename` (not full path — PII-adjacent).
- Additive — no behavior change if tracer not initialized.
- Test: extend `tests/test_artifacts.py` with an in-memory-exporter case.

### S3 — `threads.append_entry(..., kind="entry")`

- Extend signature: `append_entry(thread_id, cwd, query, summary, artifacts, turns, rationale=None, tools_used=None, scripts=None, *, kind: str = "entry")`.
- The JSONL `entry` dict now includes `"kind": kind`. No breaking change: readers that ignore unknown keys unaffected.
- `rebuild_context` leaves recent-entry rendering unchanged (reflections are context-relevant; we render them the same way for now).
- Test: extend the nearest existing threads test file (or add one if absent) to add an entry with `kind="reflection"` and assert round-trip.

### Chunk 1 exit gate

- Focused pytest on the touched modules green.
- Full `pytest -q` green (no regressions to the 531 baseline).

## Chunk 2 — three parallel pattern implementations

Dispatch three Sonnet implementers in a single message. Each gets a scoped brief: its own pattern file, its own prompt file, its own tests. None edits REGISTRY directly — they return a snippet the main thread merges.

### Chunk 2A — Pattern A: Reflection

Files:
- `src/localsmartz/patterns/reflection.py` (new)
- `src/localsmartz/agents/prompts/reflector.md` (new)
- `tests/test_pattern_reflection.py` (new, 4+ tests)

Behavior:
- `async def run(prompt, *, agents, profile, stream, runner, ctx)` matches the `Pattern` protocol.
- Flow: primary turn → reflector turn (returns JSON `{score, revision_reason, suggest}`) → if `score - prev_score < min_delta` OR score ≥ pass_threshold (0.85 default) OR `max_iterations` reached → stop. Else feed `suggest` back to primary as a revise instruction.
- Each iteration writes a `threads.append_entry(..., kind="reflection", summary=<reflector_feedback>, artifacts=[], turns=2)`.
- `validation.LoopDetector` reused as a same-output guard (if primary returns identical text twice, also stop).

Telemetry:
- Root `ls.pattern.run[pattern=reflection]`.
- Child `ls.reflection.<n>` per iteration with `ls.reflection.{index, rubric_score, revision_reason}`.
- Final attribute on root: `ls.reflection.final_score`.

F-guards in code:
- F5 compound-error: `max_iterations <= 3` hard cap.
- P3 stagnation: `min_delta=0.05` default, configurable via `budget.min_reflection_delta`.

Tests:
1. `test_reflection_span_shape` — mirror `test_pattern_span_shape.py`, 2 iterations scripted.
2. `test_reflection_stagnation_aborts` — stub returns flat scores, verify abort with `verdict=stagnant`.
3. `test_reflection_budget_exhaustion` — cap at 3 iterations even with steadily improving scores.
4. `test_reflection_threads_append_entry_kind` — stubbed `append_entry` receives `kind="reflection"`.

### Chunk 2B — Pattern B: Orchestrator-Workers

Files:
- `src/localsmartz/patterns/orchestrator.py` (new)
- `src/localsmartz/agents/prompts/worker.md` (new)
- `tests/test_pattern_orchestrator.py` (new, 6+ tests)

Behavior:
- `async def run(...)` protocol-conformant.
- Step 1 — lead agent decomposes. Decomposition output parsed to `list[WorkerTask]` + written as artifact `todo.md`.
- Step 2 — dispatch workers per Path A or Path B.
  - Path A (`profile.use_deepagents_subagents` True): invoke DeepAgents' `sub_agents` + `task` tool with the lead agent's model from S1 factory.
  - Path B: for each task, `await runner.run_turn(task.prompt, system=<worker.md + task>, model_ref=<forced to share orchestrator's if mini-override>)`. If `profile.budget.parallel_n > 1` and tier permits, wrap in `asyncio.gather`.
- Each worker output → `artifacts.register(format="markdown", title=..., ...)` — S2 ensures span emission.
- Step 3 — synthesize. Lead reads `(artifact_id, summary[:200])` for each worker only. Never raw body.
- Step 4 — emit final turn + done.

Tier gate:
- Mini tier without `profile.override_mini_orchestrator=True` → yield `{"type": "error", "content": "Orchestrator requires standard tier"}` then `done`. Unit-testable without a real model.

Depth guard:
- New `NestedSubagentError` raised if a worker attempts to re-enter `orchestrator.run`. Implemented by checking `ctx["_orchestrator_depth"] >= 1`. Contextvars avoided — explicit ctx key keeps tests simple.

F-guards in code:
- F7 telephone-game: unit test asserts the lead's synthesize prompt contains the ≤200 char summary but not the full worker text.
- F2 on mini override: worker model_refs forced to orchestrator's ref, logged.
- P1 nested: `NestedSubagentError` with test.
- P6 DeepAgents cache: `SubAgent` structs defined at module level (not per-run lambdas).

Telemetry:
- Root `ls.pattern.run[pattern=orchestrator]`.
- Children: `ls.orchestrator.plan`, `ls.orchestrator.worker.<n>` (attrs `ls.worker.{role, artifact_id, duration_ms, status}`), `ls.orchestrator.synthesize`.

Tests:
1. `test_orchestrator_span_shape` — 2-worker scripted stub, verify all 4 spans + attrs.
2. `test_orchestrator_depth_guard_raises` — feeding a worker that re-invokes raises `NestedSubagentError`.
3. `test_orchestrator_mini_tier_blocks_without_override` — mini profile without override surfaces error event + done.
4. `test_orchestrator_mini_tier_override_forces_shared_model` — override on mini, workers share lead's model_ref, log captured.
5. `test_orchestrator_reads_summaries_not_raw_output` — worker writes 2000-char artifact, assert synth prompt ≤ 200 char per worker.
6. `test_orchestrator_path_b_without_deepagents` — `use_deepagents_subagents=False`, run end-to-end on stub runner (no DeepAgents invoked).

### Chunk 2C — Pattern C: Parallelization

Files:
- `src/localsmartz/patterns/parallel.py` (new)
- `tests/test_pattern_parallel.py` (new, 4+ tests)

Behavior:
- `async def run(...)` protocol-conformant.
- Sample phase: N draws from `sampler` slot with temperatures drawn from `[0.3, 0.5, 0.7]` (first N) or `profile.parallel.temperatures` override. Enforces `>= 0.3`.
- Concurrency bound: `asyncio.Semaphore(limit)` where `limit = {mini:1, standard:2, full:3}`. Configurable via `profile.budget.parallel_concurrency`.
- Synthesize: synthesizer agent gets `N` sample bodies + user prompt, returns a merged answer. Synthesizer model picked per `config.parallel.synthesizer`.

F-guards in code:
- P4 temperature minimum: constant `PARALLEL_MIN_TEMPERATURE=0.3`, raised with clear error if violated.
- F1 OOM on mini: semaphore=1 keeps to one model load at a time.

Telemetry:
- Root `ls.pattern.run[pattern=parallel]`.
- Children `ls.sample.<n>` (attrs `ls.sample.{index, temperature, duration_ms}`) + `ls.synthesize`.

Tests:
1. `test_parallel_span_shape` — 3 samples + synth, verify span tree.
2. `test_parallel_min_temperature_enforced` — temperature 0.1 passed → raises / pattern error event.
3. `test_parallel_semaphore_limits_concurrency_mini` — stub sampler captures concurrent count, never exceeds 1 on mini.
4. `test_parallel_synthesizer_default_by_tier` — mini → same_model, standard → stronger_model.

## Chunk 3 — integration (main thread)

1. Edit `src/localsmartz/patterns/__init__.py` REGISTRY — add the 3 new entries with descriptions + required_roles + run refs.
2. Update the header docstring comment noting Phase 3 patterns are now shipped.
3. Run full test suite: `.venv/bin/python -m pytest -q` — expect ≥531 + new pattern tests, 0 failed.
4. macOS Debug build: `cd app && xcodegen generate && xcodebuild -scheme LocalSmartz -configuration Debug build`.
5. Live Phoenix smoke — one per pattern:
   - Extend `.build-loop/scripts/live-smoke.py` with `--pattern <name>` flag, defaulting to `critic_loop` for back-compat.
   - Each run: scripted prompt, verify root + child span tree in-memory, OTLP export to Phoenix if reachable.
   - Evidence: `.build-loop/evals/2026-04-23-phase-3-{reflection,orchestrator,parallel}-smoke.md`.
6. Verify `/api/patterns` returns 7 entries (unit test or curl).

## Chunk 4 — review

- Sub-step A (Critic): sonnet-critic on full diff.
- Sub-step B (Validate): span-shape + tier-gate + F-guard tests must pass; failures → memory-first gate via debugger-bridge.
- Sub-step D (Fact-check + mock-scan): parallel agents.
- Sub-step E (Simplify): `/simplify` over `patterns/`, `artifacts.py`, `threads.py`, `runners/factory.py`.
- Sub-step F (Report): final scorecard at `.build-loop/evals/2026-04-23-phase-3-scorecard.md`. Run entry via deterministic writer.

## Risks & mitigations

- **DeepAgents 0.4.x `sub_agents` API shape drift** — Path A depends on `create_deep_agent(sub_agents=...)` semantics. If the installed version differs from the Anthropic lab doc example, Path A's implementer subagent must pause and surface. Mitigation: implementer runs `python -c "import deepagents; help(deepagents.create_deep_agent)"` as first step before writing Path A code.
- **S3 `append_entry` backwards compat** — signature change. Mitigation: keyword-only `kind` param with default. No positional reorders.
- **P6 cache poisoning** — explicit note in the orchestrator implementer's brief: `SubAgent` instances at module level.
- **Phoenix smoke flakiness** — `live-smoke.py` already handles absent Phoenix via in-memory exporter fallback. Inherit that pattern for the 3 new smokes.
- **NavGator blast-radius stale** — manual grep sufficient for Phase 3. Recommend a `navgator scan` after merge (log in feedback, not blocker).

## Parallelization call

Chunks 2A/2B/2C dispatched as three concurrent `Task`/Agent calls in a single message after Chunk 1 exits green. Each subagent receives:

1. Pattern spec excerpt from this plan.
2. Relevant failure-point refs (F1/F2/F5/F7/P1/P3/P4 by pattern).
3. Canonical references: `patterns/critic_loop.py` (shape), `patterns/base.py` (protocol), `artifacts.py` (substrate), `threads.py` (persistence).
4. Test-shape reference: `tests/test_pattern_span_shape.py`.
5. Observability helper: `patterns/base.py::make_root_span`.
6. Capability routing: load `build-loop:building-with-deepagents` (Pattern B only).
7. Strict "do not edit REGISTRY" instruction — main thread does that at merge.
