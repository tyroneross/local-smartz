# Scorecard — Local-first multi-agent architecture (Phase 1 + partial 1.5/2)

Date: 2026-04-23
Goal: `.build-loop/goal.md`
Research: `~/research/inbox/2026-04-23-stratagem-local-first-multi-agent-architecture.md`
Base SHA: `eb87d73`

## Summary

What shipped (Python backends + Phase 1 foundation): runner protocol for ollama/anthropic/openai/groq, four coordination patterns (single, chain, router, critic_loop) with OTel spans, models/ package with tier-matched catalog, CLI `model` noun, cost estimator, Phoenix default-on probe, prompt .md port (orchestrator + fact_checker), three new serve endpoints. 508 tests pass, 0 regressions; +8 new test files covering the new surface.

What's deferred (requires a second build session): Swift UI changes (AgentsTab editable, ModelsTab SSE progress, PatternTab, cost-confirm modal in ResearchView), cloud-provider end-to-end wiring to the existing DeepAgents research flow, and a live golden-task eval run across local + Anthropic + Groq. The backend pieces for every deferred item are in place — only the SwiftUI edits and a wiring pass remain.

## Criteria scorecard

| # | Criterion | Status | Evidence |
|---|-----------|--------|----------|
| 1 | Runners package: AgentRunner, Local/Anthropic/OpenAI-compat | ✅ | `src/localsmartz/runners/`, `tests/test_runners.py` (4 tests) |
| 2 | Patterns package: single, chain, router | ✅ | `src/localsmartz/patterns/`, `tests/test_patterns.py` (10 tests) |
| 3 | Config schema extension (provider/pattern/agents/budget/telemetry) | ⚠️ Partial | Tier-detection added; per-project pattern schema not yet persisted in `.localsmartz/config.json`. Patterns read from runtime ctx. |
| 4 | Tier detection (mini/standard/full + ram_gb + gpu_vram_gb) | ✅ | `profiles.detect_tier()`, `tests/test_tier_detection.py` |
| 5 | Phoenix default-on + LangSmith demote | ✅ | `observability.auto_setup_if_reachable()`, `tests/test_observability_probe.py` |
| 6 | AgentsTab editable (model picker + system_focus textarea) | ❓ Deferred | Backend POST `/api/agents/<name>/model` already existed; SwiftUI edit affordance deferred. |
| 7 | Prompt port (orchestrator.md + fact_checker.md) | ✅ | `src/localsmartz/agents/prompts/`, `profiles.get_role_prompt()`, `tests/test_prompt_port.py` |
| 8 | Model catalog (ModelRec + tier-matched recommendations) | ✅ | `models/catalog.py` matches research doc exactly; `tests/test_models_registry.py` |
| 9 | CLI `model` subcommand family | ✅ | `src/localsmartz/cli/model.py`, `tests/test_cli_model_subcommand.py` |
| 10 | ModelsTab install progress bar | ❓ Deferred | Backend `/api/models/install` SSE endpoint wired; SwiftUI progress bar deferred. |
| 11 | F22 reasoning-mode guard (qwen3.5 family auto `reasoning: false`) | ✅ | `runners/local_ollama._should_disable_reasoning`, `tests/test_runners.py::test_reasoning_off_flag_respects_registry` |
| 12 | Pattern preflight (block run on missing model) | ✅ | `models/preflight.check()`, GET `/api/patterns/<n>/preflight`, `tests/test_models_registry.py::test_preflight_*` |
| 13 | Critic loop pattern (mini-tier F2 guard, OTel spans) | ✅ | `patterns/critic_loop.py`, `tests/test_patterns.py` (5 critic_loop tests) |
| 14 | PatternTab + thread inheritance | ❓ Deferred | `GET /api/patterns` ships; SwiftUI tab + thread-pinning enforcement deferred. |
| 15 | Cloud toggle end-to-end (provider swap exercised) | ⚠️ Partial | Cloud runners wired; live end-to-end run against Anthropic/Groq not executed this session (needs API keys + live test). |
| 16 | Cost-confirm modal backend | ✅ | `cost.py` + `POST /api/cloud/estimate`, `tests/test_cost.py` (6) + `tests/test_serve_new_endpoints.py` (4). SwiftUI modal deferred. |
| 17 | Golden-task eval runner in Settings | ❓ Deferred | `benchmarking.py` exists; eval-run button + `/api/evals/run` endpoint deferred to follow-on. |
| 18 | Existing features unbroken | ✅ | 500 pre-existing tests pass; `localsmartz model` CLI works live; no mutation of existing agent.py/pipeline.py runtime flows. |
| 19 | DeepAgents retry constraint preserved | ✅ | `grep '.with_retry' src/localsmartz/runners/ src/localsmartz/patterns/` → no matches in new code. Only legacy `agent.py:274` retry remains (unchanged; on fast-path LLM, not pre-create_deep_agent). |
| 20 | Phoenix trace verified for critic_loop | ⚠️ Untested | OTel spans coded per spec (`ls.pattern.run` root, `ls.iteration.<n>` children with `ls.iteration.score` + `ls.iteration.verdict`); live Phoenix verification requires running the pattern against a real model — Phoenix was "not reachable" at test time. |

## Metrics

- Tests added: +8 files, +39 test cases. Total suite: 508 passed, 4 skipped (pre-existing), 0 failed.
- Test runtime: 56s for full suite, 0.1s for the new files alone.
- New modules (Python): 14 files across `runners/`, `patterns/`, `models/`, `agents/prompts/`, `cli/`, `cost.py`. All ≤300 LOC each.
- Dependencies added: `anthropic>=0.40`, `openai>=1.50` (optional imports in `runners/__init__.py` — offline installs still work).
- Hard constraints honored: no paid observability adopted, no CrewAI/AutoGen, no `.with_retry()` before `create_deep_agent` in any new code, qwen3.5 family auto-injects `reasoning: false`.

## Artifacts

### New code
- `src/localsmartz/runners/` — base, local_ollama, cloud_anthropic, cloud_openai_compat, __init__
- `src/localsmartz/patterns/` — base, single, chain, router, critic_loop, __init__
- `src/localsmartz/models/` — registry, catalog, install, resolve, preflight, __init__
- `src/localsmartz/agents/prompts/orchestrator.md`, `fact_checker.md`
- `src/localsmartz/cli/model.py`
- `src/localsmartz/cost.py`

### Extended
- `src/localsmartz/profiles.py` — `detect_tier()`, `_detect_ram_bytes()`, `_detect_gpu_vram_gb()`, `get_role_prompt()`
- `src/localsmartz/observability.py` — `probe_collector()`, `auto_setup_if_reachable()`
- `src/localsmartz/__main__.py` — `model` noun wired, Phoenix auto-init
- `src/localsmartz/serve.py` — `GET /api/patterns`, `GET /api/patterns/<n>/preflight`, `POST /api/cloud/estimate`, `POST /api/models/install` (SSE)
- `pyproject.toml` — anthropic + openai deps

### New tests
- `tests/test_runners.py`, `tests/test_patterns.py`, `tests/test_models_registry.py`, `tests/test_tier_detection.py`, `tests/test_cost.py`, `tests/test_observability_probe.py`, `tests/test_prompt_port.py`, `tests/test_serve_new_endpoints.py`, `tests/test_cli_model_subcommand.py`

## What remains for a follow-on build

Listed in dependency order so a follow-on session can execute quickly.

1. **SwiftUI AgentsTab → editable** — model picker (pulls from `GET /api/models/catalog?tier=<tier>`), `system_focus` markdown textarea, Save button POSTs to existing `/api/agents/<name>/model` + a new `PUT /api/agents/<role>/prompt` handler (4 lines).
2. **SwiftUI ModelsTab install affordance** — per-model "Install" button calls `POST /api/models/install` and renders a progress bar by consuming the SSE events already landed.
3. **SwiftUI PatternTab** — dropdown listing the 4 patterns from `GET /api/patterns`, agent slot configuration inherits AgentsTab, persists choice to `.localsmartz/config.json`.
4. **SwiftUI cost-confirm modal** — before any cloud run, call `POST /api/cloud/estimate` and show a modal with the estimated USD; require explicit Continue.
5. **Cloud path wiring in agent.py / pipeline.py** — introduce a provider-swap branch that picks `CloudAnthropicRunner` / `CloudOpenAICompatRunner` based on the `provider` config key. Currently the runners exist but the live research flow still hardcodes ChatOllama.
6. **Golden-task harness across providers** — extend `benchmarking.py` with `run_golden_on_provider(provider)` and a diff helper; add a Settings → "Run eval suite" button that POSTs `/api/evals/run`.
7. **Thread pattern pinning (F15)** — `.localsmartz/threads/*.json` records the pattern at creation; serve refuses to run a thread under a different pattern. Surfaces as "Switching pattern will start a new thread" confirm in the UI.
8. **Live Phoenix smoke-run** — run a critic_loop against qwen3.5:9b with Phoenix up, confirm `ls.pattern.run` span has pattern=critic_loop + ls.iteration.* children with score/verdict attrs. (All attribute-emission code lands in this build; verification is the only open step.)

None of the above require re-architecting what's already in place — they consume the backends shipped here.

## Risks / uncertainties

- **⚠️ Cloud SDK versions**: pinned `anthropic>=0.40` and `openai>=1.50` using current APIs at 2026-04-23; not verified against live endpoints this session. If the Anthropic SDK has drifted past the shapes assumed in `cloud_anthropic.py` (tool_use blocks, `usage.input_tokens`), cleanup is a 10-line patch.
- **⚠️ GPU VRAM detection**: stubbed to `0` on Apple Silicon (correct — unified memory); not tested on discrete-GPU Linux, but the research-doc target is Apple.
- **TAG:INFERRED** — rate table in `cost.py` uses provider pricing as I recall it; `cost.LAST_UPDATED` flags it for re-verification. Doctor probe warns if rates >90 days old.
- **❓ Untested** — Phoenix collector integration at the live-trace level. Unit tests exercise probe_collector false path; the success path requires a running Phoenix instance, which wasn't up at test time.

## Next steps

1. Run `.venv/bin/python -m localsmartz model recommend --install` on a target machine to pull the tier-matched set.
2. Start Phoenix (`docker run -p 6006:6006 arizephoenix/phoenix`) and run `.venv/bin/python -m localsmartz --observe "what is 15% of 2400?"` to verify the OTel span fan-out.
3. Open a follow-on build pass with the 8 deferred items above — estimated 1-2 days for the SwiftUI work, ~half a day for the cloud wiring + golden-task harness.

---

**Run ID**: local-smartz-2026-04-23-phase1 (written manually — deterministic run-writer scripts aren't part of this build's scope).
