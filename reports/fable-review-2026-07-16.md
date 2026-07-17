# Local Smartz — Fable 5 review & enhancement recommendations

Date: 2026-07-16 · Reviewer: Claude Fable 5 (session review) · Repo @ edf379f
Inputs: 3 parallel code-map explorations (model flow, app UI, cloud integration),
9 agent prompt files, 5-task live comparison vs rosslabs-agent-harness (two legs:
as-configured + fixed-config), plan-critic findings.

## Headline
Local Smartz has unusually complete plumbing (per-agent models, cloud runners,
secrets, patterns, 830-test suite) but three trust-breaking runtime defects and
several dormant layers: it can claim success without acting, ship settings the
runtime ignores, and pass `--check` on a model that 404s at run time.

## Comparison evidence (5 moderate tasks, <2 min budget, details in judge report)
- harness: 3/5 oracle-pass, avg ~63s, structured JSONL session logs.
- smartz as-configured: 0/5 — hardcoded execution-model tag not installed (404).
- smartz fixed-config: 0/5 — four 150s timeouts (multi-agent pipeline + model
  loads exceed the envelope), one advisory-only "success".
- smartz t2 both legs: exit 0 in ~10s with a *plan* ("Requires human review")
  while pytest still failed — a false success signal.
- smartz `--quiet` produced zero output for 150s — no heartbeat; hung vs working
  indistinguishable.

## Defects fixed this run (build-loop C1, verified by tests)
1. `profiles.py:191,202` hardcoded `qwen2.5-coder:32b-instruct-q5_K_M` (not
   installed) → corrected tag + availability fallback for execution/agent models
   (previously only the planning model had fallback).
2. `--check` reported the missing tag "ready" → exact-tag validation vs Ollama.
3. Prompt activation gap: runtime read embedded `system_focus`
   (`agent.py:591,735,755`, `pipeline.py:136`) while the UI edited and displayed
   `agents/prompts/*.md` — user prompt edits silently ignored → all call sites
   now route through `get_role_prompt` (.md first, fallback preserved).
4. Orphan `active_model` global setting (written by UI, read by nothing) → wired
   as single-model mode.

## Features added this run
- Custom agent selection (`disabled_agents` + UI toggles + CLI + validation).
- Global one-model setting (`active_model` applied with clear precedence).
- Local-Only toggle (`local_only`) enforced at all three runtime model-construction
  choke points (LocalOnlyError) + four HTTP endpoints + secrets env-export skip +
  UI provider hiding.
- 9 agent prompts rewritten with prompt-builder small-model calibration
  (numbered procedures, end-positioned output contracts, few-shot for the two
  JSON emitters, explicit degradation formats).

## Recommendations (not implemented this run, ranked)
1. **Kill false success in the coding route.** The guarded coding loop
   (`coding_loop.py` / `coding_harness.py`, commits ee6e772/edf379f) returns
   advisory prose with rc=0 for mutation tasks. Either apply patches behind a
   test gate (see rec 2) or exit with distinct status + an explicit
   `ANALYSIS-ONLY` banner. Highest-priority trust fix; a scheduler or script
   consuming rc=0 today believes work happened.
2. **Adopt oracle-gated execution (the harness's core win).** The harness passed
   3/5 because it executes real tool calls and (when given `--verify`) gates on
   a deterministic oracle. Smartz should run doctest/pytest/json-parse after
   code/data tasks and only claim success on green — its 0/5 was not model
   quality, it was missing gating (same models are available to both).
3. **Latency envelope + fast lane.** Four timeouts at 150s. Wire
   `routing.select_research_runtime`/`select_agent_roles` (exists, explicitly
   unwired) so file-based deterministic tasks (CSV math, extraction) route to a
   single python_exec agent instead of the full planner→researcher→fact-checker
   pipeline. Target: moderate tasks < 60s warm.
4. **Progress heartbeat.** `--quiet` should still emit a stderr status line
   (stage + elapsed + model) every ~10s; the app's SSE already has this signal —
   expose it in CLI.
5. **Always-on run log.** A workspace run leaves only `.localsmartz/config.json`
   behind. Write a per-run JSONL (prompt, route, model, tool calls, timings,
   final status) under `.localsmartz/runs/` — the harness's event-sourced
   session log made its runs auditable in minutes; `log_buffer.py`/
   `observability.py` already hold most of the machinery.
6. **Settings consolidation.** Merge AgentsTab + AgentRoutingTab (same endpoint,
   duplicated UI); migrate remaining direct global.json writes to
   `/api/settings`; delete orphan schema fields after migration.
7. **Remove or wire the parallel resolution layer.** `models/resolve.py::
   resolve_role_model` + registry tier defaults + project-config `model_ref` form
   a second, unused role→model system alongside `get_agent_model`. Two sources of
   truth will drift; pick one.
8. **Split serve.py** (~3.5k lines: HTTP routing + business logic + SSE). Extract
   handlers into modules; keeps the endpoint surface testable and reviewable.
9. **Embedded-vs-file prompt cleanup.** After the get_role_prompt unification
   soaks, shrink `AGENT_ROLES.system_focus` to one-line summaries and make the
   .md files the single authored source (system_focus remains only as a packaged
   fallback).
10. **Cross-check secrets key naming.** `factory._cloud_api_key` reads
    `<provider>_api_key` while `cloud_anthropic` reads `get_secret("anthropic")`
    — unify to one helper so a key stored via the UI is found by every runner.

## Verification status
- ✅ Comparison legs + oracles: deterministic scripts (runner.py, verify.py),
  artifacts under scratchpad/compare/.
- ✅ Code findings: file:line cites from read code (three explorer maps).
- ⚠️ C1/C2/C3 outcomes summarized above are per-implementer reports + orchestrator
  spot-checks; full-suite pytest + xcodebuild + live smoke are the gate before
  commit (see build run report).
