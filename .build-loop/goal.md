# Goal — Phase 3 multi-agent patterns (Reflection, Orchestrator-Workers, Parallelization)

Authoritative research: `.build-loop/research/2026-04-23-phase-3-plan.md`
Failure-point catalog: `~/research/inbox/2026-04-23-stratagem-local-first-multi-agent-architecture.md`
Prior phase scorecard: `.build-loop/evals/2026-04-23-local-first-multi-agent-followup-scorecard.md`

## Bottom line

Ship three new coordination patterns in local-smartz — Reflection, Orchestrator-Workers, and Parallelization — along with three shared refactors (S1 factory extract, S2 artifact spans, S3 threads.append_entry `kind` param) that land first. All patterns reuse `artifacts.py` + `threads.py` as the context-isolation substrate. Preserve the 24GB mini-tier floor, Phoenix+OTel observability, and the `create_deep_agent` RunnableRetry constraint.

## Criteria (must all be ✅ at Review-F)

### Shared refactors (chunk 1, on main thread, MUST land first)

1. **S1 runner factory** — `src/localsmartz/runners/factory.py` exposes `create_langchain_model(provider, model_ref) -> ChatModel`. `agent.py::_create_model` delegates to this factory. qwen3.5 auto `reasoning: false` injection preserved. No `.with_retry()` wrapping. Unit test: factory returns a `ChatOllama` for ollama, `ChatAnthropic` for anthropic, `ChatOpenAI` for openai/groq.
2. **S2 artifact spans** — `artifacts.register()` emits `ls.artifact.register` span with attrs `ls.artifact.{id, format, thread_id, path_basename}`. Non-breaking (span is additive). Unit test: in-memory exporter captures the span.
3. **S3 threads.append_entry `kind`** — `threads.append_entry(...)` accepts optional `kind: str = "entry"` parameter written into the JSONL record. Existing callers unchanged. Unit test: entry with `kind="reflection"` persists and is round-trippable.

### Pattern A: Reflection (`patterns/reflection.py`)

4. **Reflection pattern registered** — `REGISTRY["reflection"]` exists with description, `required_roles=["primary", "reflector"]`, and `run` entrypoint. `/api/patterns` lists it.
5. **Single-agent self-critique loop** — Same model generates → reflects → revises. `profile.budget.max_iterations` (default 3) + `max_turns` (default 6) honored. Works on mini tier without co-residency.
6. **Stagnation detection (P3 guard)** — Rubric score must improve by `min_delta` (default 0.05) between iterations. If not, loop aborts early with `verdict=stagnant`. Reuses `validation.LoopDetector` class shape where sensible. Unit test exercises a scripted stub that stagnates.
7. **Span shape (Reflection)** — Root `ls.pattern.run[pattern=reflection]` with `ls.tier`, `ls.provider`, `ls.thread.id`. Children `ls.reflection.<n>` with attrs `ls.reflection.index`, `ls.reflection.rubric_score`, `ls.reflection.revision_reason`. Unit test: in-memory exporter regression.
8. **Reflection persistence** — Each reflection append written to `threads.append_entry(..., kind="reflection")`. Unit test verifies entry written with kind.
9. **Reflector prompt** — `src/localsmartz/agents/prompts/reflector.md` exists, referenced by pattern default system, loaded via the existing `load_prompt()` path.

### Pattern B: Orchestrator-Workers (`patterns/orchestrator.py`)

10. **Orchestrator pattern registered** — `REGISTRY["orchestrator"]` exists with `required_roles=["orchestrator", "worker"]`.
11. **Hardcoded depth=1 (P1 guard)** — A new `NestedSubagentError` is raised if a worker attempts to spawn a sub-worker. Unit test: constructing a worker that tries to invoke the pattern again raises.
12. **Dual implementation path** — `profile.use_deepagents_subagents` dispatches: `True` → DeepAgents `sub_agents`+`task` path (Path A); `False` → iterative `AgentRunner.run_turn` (Path B). Default True on standard+/cloud, False on mini. Selection logged at INFO.
13. **Tier gate** — Mini tier blocks orchestrator by default with a warning; requires explicit `profile.override_mini_orchestrator=True`. Unit test: mini tier without override raises or emits a clear error event.
14. **F7 telephone-game mitigation** — Orchestrator reads **artifact IDs + ≤200-char summaries only** (never raw worker output) when synthesizing. Verified in code + unit test: a worker writes a 2000-char artifact; orchestrator's synthesize prompt contains only the summary truncated to ≤200 chars.
15. **todo.md recitation (Manus pattern)** — Lead agent writes decomposed plan via `artifacts.register(format="markdown", title="todo.md", ...)` before dispatching workers.
16. **F2 guard on mini** — If override is used on mini, all workers share orchestrator's model ref. Logged at INFO. Unit test covers.
17. **Span shape (Orchestrator)** — Root `ls.pattern.run[pattern=orchestrator]` with standard attrs. Children: `ls.orchestrator.plan` + `ls.orchestrator.worker.<n>` (attrs `ls.worker.role`, `ls.worker.artifact_id`, `ls.worker.duration_ms`, `ls.worker.status`) + `ls.orchestrator.synthesize`. Unit test regression.
18. **Worker prompt** — `src/localsmartz/agents/prompts/worker.md` exists.

### Pattern C: Parallelization (`patterns/parallel.py`)

19. **Parallel pattern registered** — `REGISTRY["parallel"]` exists with `required_roles=["sampler", "synthesizer"]`.
20. **Tier-gated concurrency** — Mini: semaphore=1 (sequential, varied temperature/persona). Standard: semaphore=2. Full: semaphore=3. `profile.budget.parallel_n` (default 3) controls N. Unit test confirms semaphore limit honored.
21. **P4 temperature ≥ 0.3** — Samples enforce `temperature >= 0.3` (else raises / warns). Unit test.
22. **Synthesizer config** — `config.parallel.synthesizer` one of `same_model | stronger_model | user_select`. Default `same_model` on mini, `stronger_model` on standard+. Unit test covers both defaults.
23. **Span shape (Parallel)** — Root `ls.pattern.run[pattern=parallel]` → children `ls.sample.<n>` + `ls.synthesize`. Unit test regression.

### Cross-pattern

24. **Phase 3 pattern list in `/api/patterns`** — `single`, `chain`, `router`, `critic_loop`, `reflection`, `orchestrator`, `parallel` — 7 patterns returned by `GET /api/patterns`. `required_roles` correct for each.
25. **PatternTab sees 7 patterns** — SwiftUI picker enumerates all 7. Tier-gate indicator visible for orchestrator (standard+) and parallel (semaphore differs by tier, not blocked). No Swift rebuild required beyond what `/api/patterns` already feeds.
26. **Live Phoenix smoke for each pattern** — 3 new smoke scripts at `.build-loop/scripts/smoke-{reflection,orchestrator,parallel}.py` (or extended `live-smoke.py --pattern`). Evidence files at `.build-loop/evals/2026-04-23-phase-3-{reflection,orchestrator,parallel}-smoke.md` with captured span tree. Mini tier's orchestrator smoke documents override path.
27. **Test suite green** — `pytest -q`: ≥531 passing, 0 failed (4 skipped pre-existing acceptable). New tests cover span shape, tier gating, budget exhaustion, stagnation, depth-1, temperature minimum.
28. **macOS Debug build** — `xcodebuild -scheme LocalSmartz -configuration Debug build` → `BUILD SUCCEEDED`.
29. **DeepAgents retry constraint preserved** — Static grep: no `.with_retry(` calls appear before `create_deep_agent(` on any chat model in `src/localsmartz/{patterns,runners,agent,pipeline}.py`. Existing check extended to new files.
30. **qwen3.5 reasoning-off guard preserved** — S1 factory path maintains the `reasoning: false` injection for qwen3.5 family. Unit test.

## Hard constraints

- 24GB M4 floor preserved.
- No paid observability (Phoenix + OTel only).
- No `.with_retry()` before `create_deep_agent` anywhere (including S1 factory).
- qwen3.5 auto `reasoning: false` injection survives the S1 refactor.
- Hardcoded `max_depth=1` in orchestrator path (both A and B).
- Temperature minimum 0.3 for parallel samples.
- Orchestrator reads artifact summaries only, never raw worker output.
- Pattern A/B/C implementation-independent — dispatch to parallel subagents in Execute phase after chunk 1 lands.

## Non-criteria (out of scope this build)

- Debate pattern (deferred to v4).
- Dynamic agent creation (deferred to v3).
- Logit masking adapter (research packet listed as optional; defer to Phase 3.5).
- PII-filter span processor (research packet optional; defer unless a reviewer flags it).
- Catalog gap (`gpt-oss:{20b,120b}`) — already refreshed per task brief.
- Renaming any existing role prompt.
- Changing orchestrator.md prompt content (would be existing-prompt edit; out of scope).
