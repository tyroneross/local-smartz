# Plan — Phases 1 + 1.5 + 2

## Scope summary
Goal: `.build-loop/goal.md`. Research: `~/research/inbox/2026-04-23-stratagem-local-first-multi-agent-architecture.md`.

## Dependency graph

```
Phase 1 — Foundation (parallel lanes A, B, C, D)
├── A. Runners package            → (no deps)
├── B. Patterns package           → depends on A (runner protocol)
├── C. Config schema + tier       → (no deps)
├── D. Prompt port (.md files)    → (no deps)
└── E. AgentsTab editable         → depends on C (config) + new API in serve.py

Phase 1.5 — Model install (parallel lanes F, G)
├── F. models/ package            → (no deps — wraps existing ollama.py)
├── G. CLI `model` subcommand     → depends on F
└── H. ModelsTab install progress → depends on F, G

Phase 2 — Critic loop + cloud (sequential)
├── I. critic_loop pattern        → depends on A, B
├── J. Cloud runners wired        → depends on A (already built in Phase 1 as protocol; wire provider swap here)
├── K. PatternTab UI              → depends on C, E, I
├── L. Cost-confirm modal         → depends on J
└── M. Golden-task eval button    → depends on existing benchmarking.py

Phase 1 observability fold-in (ships with Phase 1)
├── N. Phoenix default-on + LangSmith demote
└── O. Doctor Phoenix probe
```

## Chunks (each atomic, testable, reversible)

### Phase 1

#### Chunk 1A — runners package
- Files: `src/localsmartz/runners/__init__.py`, `base.py`, `local_ollama.py`, `cloud_anthropic.py`, `cloud_openai_compat.py`.
- Deliver: `AgentRunner` protocol with `async def run_turn(prompt, tools, model_ref, ctx) -> AssistantTurn`. `ModelRef = TypedDict({"provider","name","base_url?"})`. LocalOllamaRunner wraps existing `_create_model` + ChatOllama flow. Cloud runners are thin adapters — Anthropic SDK via `anthropic` pkg, OpenAI-compat via `openai` pkg pointed at `base_url` (OpenAI default, Groq with `https://api.groq.com/openai/v1`). Never wrap in `.with_retry()` before create_deep_agent.
- Add deps to pyproject.toml: `anthropic>=0.40`, `openai>=1.50`.
- Tests: `tests/test_runners.py` — mock transport for each; assert payload shape; verify reasoning-mode flag injection path (see 1F).

#### Chunk 1B — patterns package (single, chain, router)
- Files: `src/localsmartz/patterns/__init__.py`, `base.py`, `single.py`, `chain.py`, `router.py`.
- Deliver: `Pattern` protocol, `async def run(prompt, agents, profile, stream, runner) -> AsyncIterator[dict]`. `single.py` = existing agent flow wrapped. `chain.py` = linear stage pipeline (gather → analyze → write). `router.py` = classifier-picks-specialist. Events: `{"type": "pattern_start|turn|tool|iteration|done", ...}`.
- Tests: `tests/test_patterns.py` — each pattern runs with a stub runner that returns canned turn dicts. Integration test that `single` wraps the existing `run_research` behavior so zero regression.

#### Chunk 1C — config schema + tier detection
- Files: edit `src/localsmartz/config.py`, `src/localsmartz/global_config.py`, `src/localsmartz/profiles.py`, `src/localsmartz/utils/hardware.py`.
- Deliver: schema keys `provider`, `pattern`, `agents{role:{model_ref,system_focus_path,tools}}`, `budget{max_turns,max_iterations}`, `telemetry{phoenix,trace_tags}`. Migration: old flat `planning_model` still supported (auto-wrapped into agents[*].model_ref). `detect_profile()` returns dict with `tier` in `{mini, standard, full}` using RAM cutoffs from research doc: `<32GB=mini, 32-96=standard, >=96=full`. Adds `detect_gpu_vram_gb()` stub (returns 0 for CPU-only, best-effort Metal detection on macOS — non-blocking).
- Tests: `tests/test_tier_detection.py`, extend `tests/test_config.py`, `tests/test_global_config.py`.

#### Chunk 1D — prompt port (.md files)
- Files: create `src/localsmartz/agents/prompts/orchestrator.md`, `src/localsmartz/agents/prompts/fact_checker.md` (reviewer.md already exists — leave). Edit `src/localsmartz/profiles.py` to remove the `system_focus` string body from `AGENT_ROLES` and add a helper `get_role_prompt(role) -> str` that reads .md via `load_prompt(role)` with fall-back to any retained string.
- Tests: verify each role's .md file loads non-empty via `load_prompt` and `AGENT_ROLES[role]["system_focus"]` proxies correctly. Snapshot test: new body must contain the existing anti-hallucination / MAIN-ORCHESTRATOR guardrails verbatim.

#### Chunk 1E — AgentsTab editable + serve endpoint
- Files: `app/LocalSmartz/Views/AgentsTab.swift` (promote read-only → editable with per-agent model picker + system_focus textarea + Save), edit `src/localsmartz/serve.py` to add `POST /api/agents/<role>` accepting `{model_ref?: dict, system_focus?: str}`. Preserve existing `POST /api/agents/<name>/model`.
- Model picker must call `/api/models/catalog?tier=<tier>` (already wired) and show only tier-compatible.
- Tests: `tests/test_serve.py::test_api_post_agent_role_config` — unit test for endpoint. Swift-side UI guarded behind existing UI smoke test.

#### Chunk 1N — Phoenix default-on + LangSmith demote
- Files: edit `src/localsmartz/observability.py` (add `probe_collector(endpoint) -> bool` with 1-sec HTTP HEAD; auto-call `setup_observability()` when reachable). Edit `src/localsmartz/__main__.py` to remove `--trace` as default and remove `--observe` as opt-in (flip: `--no-trace` and `--no-observe` opt-outs). Edit `src/localsmartz/tracing.py` — gate all LangSmith init behind `--trace` flag explicitly.
- Doctor: edit `src/localsmartz/doctor.py` to probe Phoenix at 6006.
- Tests: extend `tests/test_tracing.py` to assert LangSmith does NOT auto-init when flag absent.

### Phase 1.5

#### Chunk 1.5F — models/ package
- Files: `src/localsmartz/models/__init__.py`, `catalog.py`, `registry.py`, `install.py`, `resolve.py`, `preflight.py`.
- `registry.py`: `ModelRec` TypedDict exactly matching research doc spec (name/family/params_b/size_gb_q4/tier/roles/tool_reliability/reasoning_mode/capabilities/context_window/notes/default_for).
- `catalog.py`: hardcoded tier-matched set from research doc:
  - mini: qwen3.5:9b (primary fast+strong), qwen3.5:4b (optional tiny router)
  - standard: qwen3.5:9b (fast), qwen3.5:27b (strong), qwen3-coder-next:30b (coder), gemma4:26b (vision alt)
  - full: qwen3.5:9b (router), qwen3.5:122b (strong), qwen3-next:80b (reasoning critic)
  - cross-tier: qwen3-coder-next:30b, lfm2.5-thinking:1.2b, bge-base-en-v1.5
  - Each entry has `reasoning_mode: "off-by-default"` on qwen3.5 family (F22).
- `install.py`: wraps existing `ollama.stream_pull`; emits OTel span `ls.model.install` with attrs for name + bytes + duration.
- `resolve.py`: role → concrete model substitution. On mini tier: if `writer != critic`, force `critic = writer` with INFO log (F2).
- `preflight.py`: `check(pattern, profile) -> {ok, missing, suggestions}`. UI reads to block pattern open.
- Tests: `tests/test_models_registry.py`, `tests/test_preflight.py`, `tests/test_resolve.py`, `tests/test_models_install.py`.

#### Chunk 1.5G — CLI `model` subcommand
- Files: edit `src/localsmartz/__main__.py` to register `model` noun. New file `src/localsmartz/cli/model.py` with subcommands list/recommend/recommend --install/add/remove/assign/doctor. Share install function with serve via `models.install.install()`. Progress to stderr with single-line rewrite (no tqdm dep — manual `\r`).
- Tests: extend `tests/test_cli_subcommands.py` with each subcommand driven by a stub `stream_pull`.

#### Chunk 1.5H — ModelsTab install progress
- Files: edit `app/LocalSmartz/Views/ModelsTab.swift`. Add "Install recommended set" primary CTA. Per-row install button calls `POST /api/models/install` SSE; render progress bar from `downloaded/total` events. Cancel button. Add `/api/models/install` handler in `serve.py` wrapping `models.install.install()`.
- Tests: `tests/test_serve.py::test_api_models_install_sse` — assert SSE events have correct shape.

### Phase 2

#### Chunk 2I — critic_loop pattern
- Files: `src/localsmartz/patterns/critic_loop.py`.
- Flow: writer generates → critic scores against a small rubric (JSON-structured) → if score < threshold, refine with critic feedback → loop until score ≥ threshold OR iterations exhausted OR turn budget exhausted. Re-uses `validation.LoopDetector` as escape hatch.
- Mini-tier: force `critic_model == writer_model` at runner dispatch; log "tier=mini forced critic=writer (F2)" at INFO.
- Telemetry: root `ls.pattern.run` with `pattern=critic_loop`, `ls.tier`, `ls.provider`, `ls.thread.id`. Child spans per iteration with `ls.iteration.score`, `ls.iteration.verdict`, `ls.iteration.index`.
- Tests: `tests/test_critic_loop.py` — stub runner; assert iteration stops on threshold, iteration cap, and turn cap; assert mini-tier model forcing; snapshot span attrs.

#### Chunk 2J — cloud runner wiring (provider swap)
- Files: edit `src/localsmartz/runners/cloud_anthropic.py` + `cloud_openai_compat.py` finished; edit pattern dispatch so `provider` in config selects runner. Anthropic key from keyring (existing `secrets.py`). Groq/OpenAI keys same path with different service names.
- Golden-task harness: extend `benchmarking.py` with `run_golden_on_provider(provider)` and a diff helper; run on local + Anthropic + Groq.
- Tests: `tests/test_cloud_runners.py` with mocked SDKs; `tests/test_provider_swap.py`.

#### Chunk 2K — PatternTab + thread inheritance
- Files: `app/LocalSmartz/Views/PatternTab.swift` (new) OR fold into SettingsTabs; wire dropdown to `GET /api/patterns`, `POST /api/patterns/active`. ResearchView.swift: on pattern change detection vs. thread, show confirm modal "Switching pattern will start a new thread. Continue?" → creates new thread.
- Backend: new endpoints in `serve.py`: `GET /api/patterns` (list of registered patterns), `POST /api/patterns/active` (set project pattern), `GET /api/patterns/<name>/schema` (slot requirements). Threads module pins `pattern` at thread creation; refuses to run a thread under a different pattern.
- Tests: `tests/test_pattern_api.py`, extend `tests/test_threads.py` for pattern-pin behavior.

#### Chunk 2L — cost-confirm modal
- Files: new `src/localsmartz/cost.py` with per-provider rate table (Anthropic, OpenAI, Groq) + token estimator (prompt char count × 0.25 → tokens; output cap × 2× prompt). New `GET /api/cloud/estimate?provider=&prompt=&pattern=` endpoint. Edit `app/LocalSmartz/Views/ResearchView.swift` — before any cloud run, call estimate, show modal with "Est. $0.XX", require explicit Continue.
- Last-updated date embedded in table; doctor warns if rates >90 days old.
- Tests: `tests/test_cost.py`.

#### Chunk 2M — golden-task eval button
- Files: edit `app/LocalSmartz/Views/SettingsTabs.swift` + new `EvalTab.swift` (or fold): "Run eval suite" button posts to `/api/evals/run`. Wire to `benchmarking.py::run_golden_on_provider`. Results shown as pass/fail per task with OTel span `ls.eval.run`.
- Tests: `tests/test_eval_api.py`.

## Execute dispatch plan (parallel subagents)

**Pass 1 (parallel)**: Chunks 1A, 1C, 1D, 1F, 1N. Each subagent gets: task description, relevant file paths, integration contract, the research doc excerpt for its slice, and DeepAgents retry constraint.

**Pass 2 (parallel, needs Pass 1)**: Chunks 1B (needs 1A), 1E (needs 1C), 1.5G (needs 1F), 1.5H (needs 1F+1G). Dispatched after Pass 1 critic review.

**Pass 3 (sequential, Phase 2)**: 2I → 2J → 2K/2L/2M in parallel.

Each subagent:
- Model: Sonnet, effort medium per build-loop:model-tiering defaults.
- Writes tests with every code change. Tests must pass before Critic hands back.
- Doesn't edit outside its listed files without recording rationale in `.build-loop/issues/`.

## Risk gates

- Pass-1 critic checkpoint: runner protocol signature must be final before patterns are built (Pass 2). If 1A changes shape, 1B re-dispatch.
- Phase 2 golden-task diff gate: if local vs Anthropic vs Groq diff exceeds subjective-quality threshold, surface in Review-F but do NOT block Phase 2 completion (research doc says "diff reported", not "diff blocks").
- Cost-confirm modal must be wired BEFORE any cloud runner path can execute from the UI. Test order matters — gate this in integration tests.

## Evidence we'll collect for Review-F

- `pytest tests/` full pass
- `localsmartz --serve` starts, `/api/status` returns 200
- macOS app builds: `xcodebuild -project app/LocalSmartz.xcodeproj -scheme LocalSmartz -configuration Debug build`
- `localsmartz model list` returns non-empty on installed Ollama
- Phoenix shows `ls.pattern.run` span with critic_loop attrs
- Golden task runs on local + Anthropic (Groq if API key available)
- Grep check: no `.with_retry(` between `ChatOllama(` and `create_deep_agent(` in runtime code
