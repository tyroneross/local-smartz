# Plan — Agent/model settings enhancements + prompt improvements (2026-07-16)

Prior plan archived: build-loop-memory/projects/local-smartz/archive/plans/2026-07-17/plan.md

## Intent
North star: local-first multi-agent research app whose agent roster, models, and
privacy posture are user-controllable from Settings.
Update intent: (1) improve the 9 agent prompts for the small-local-model tier via
prompt-builder; (2) let users choose WHICH agents run; (3) per-agent model selection
(exists — verify; tab consolidation DESCOPED to a recommendation) plus a working
global one-model setting; (4) a
local-only toggle that blocks cloud model selection AND invocation; (5) fix defects
found during the harness comparison (execution-model 404, --check false-ready).

Non-goals: new cloud providers, new agent roles, redesigning the pattern system,
macOS app visual redesign.

## Evidence (Phase 1 findings, file:line)
- `active_model` is an orphan: schema + Swift write it (`global_config.py:24`,
  `SettingsView.swift:145`), no Python reader applies it.
- Per-agent model selection fully plumbed: `profiles.py:366-382` (get_agent_model),
  CLI `model assign`, `POST /api/agents/<name>/model`, two UI tabs (AgentsTab,
  AgentRoutingTab — redundant).
- No agent participation selector: `_build_subagent_specs` (`agent.py:558-611`)
  always registers all non-orchestrator roles; `routing.select_agent_roles` exists
  but is unwired.
- No local_only flag anywhere; cloud reachable via 3 runtime paths:
  `serve.py:2543-2553` (_run_pattern_cloud), `runners/__init__.py:40-62` +
  `runners/factory.py:44-77`, `agent.py:253-281` (_create_model/_active_provider).
- Defect: full profile hardcodes `qwen2.5-coder:32b-instruct-q5_K_M`
  (`profiles.py:191,202`) — not pulled; runtime 404 after 94s (comparison run t1/t3);
  planning-model fallback (`resolve_available_model`) not applied to execution/agent
  models; `--check` reported this model "ready".

## Frozen backend contract (interface freeze for parallel fan-out)
global.json new/changed keys (all read live by backend via load_global()):
- `local_only: bool` (default false) — NEW schema key (bool type).
- `disabled_agents: list[str]` (default []) — NEW schema key. Role names from
  AGENT_ROLES minus `orchestrator` (orchestrator not disableable).
- `active_model: str` — existing key, NOW APPLIED: when non-empty, single-model
  mode — it is the effective model for planning/fast/execution and every agent
  role. Precedence: CLI `--model` (per-run) > `active_model` > serve
  select/project `planning_model` (planning) / `agent_models[role]` (roles) >
  profile defaults.

HTTP endpoints (serve.py):
- `GET /api/settings` → `{"local_only": bool, "active_model": str,
  "disabled_agents": [str], "profile": str}`
- `POST /api/settings` body: any subset of the three keys → validates
  (active_model installed-or-empty; disabled_agents ⊆ known roles, never all
  runnable roles disabled, orchestrator rejected) → persists to global.json →
  returns the full settings object. Error: `{"error": "<msg>"}` HTTP 400.
- `POST /api/agents/<name>/enabled` body `{"enabled": bool}` → updates
  disabled_agents → `{"ok": true, "agent": str, "enabled": bool}`.
- `GET /api/agents` — each agent gains `"enabled": bool`.
- Local-only enforcement responses: HTTP 403 `{"error": "local_only",
  "detail": "cloud providers disabled by Local-Only setting"}` from
  `/api/patterns/active` (cloud provider), `/api/evals/run` (cloud provider),
  research dispatch when persisted provider is cloud (coerce to ollama + warn
  event), `/api/cloud/estimate`.

Runtime enforcement (raise `LocalOnlyError(RuntimeError)` defined in
`runners/base.py`): `runners.get_runner`, `runners.factory.create_langchain_model`,
`agent._create_model` — deny provider != "ollama" when `local_only` true.

Additional frozen shapes (drift vectors closed):
- `GET /api/models` (existing, unchanged): `{"models": [{"name": str,
  "size_gb": float}], "current": str, "profile": str}` — C3 codes against this.
- `POST /api/agents/<name>/enabled` errors: HTTP 400 `{"error": "<msg>"}`
  (unknown role, orchestrator, would-disable-all) — same shape as /api/settings.
- `GET /api/status` (C1-owned change): gains `"active_model": str,
  "local_only": bool`; `effective_model` reflects active_model when set.
- fact_checker output JSON (FROZEN — C2 must emit prompts producing exactly):
  `{"verdict": "ok" | "needs_more", "missing_facts": [str]}`.
- reflector output JSON (FROZEN): `{"score": float, "revision_reason": str,
  "suggest": str}`.
- qwen2.5-coder:32b confirmed installed this session (`GET /api/tags` listed it,
  19.9 GB) — safe as corrected full-profile execution default.

## Activation Map
- LocalOnlyError: raised at runners/__init__.py get_runner, runners/factory.py
  create_langchain_model, agent.py _create_model → activated on every model
  construction; verified-live by unit tests + live serve smoke (403 path).
- disabled_agents filter: agent.py _build_subagent_specs, pipeline.py node
  builders, profiles.py list_agents → activated on every research run + GET
  /api/agents; verified-live by smoke run with one agent disabled.
- active_model pin: profiles.py get_model/get_agent_model (+ agent.py override
  path) → activated on every model resolution; verified in /api/status.
- CLI subcommands: cli/model.py dispatch (existing `localsmartz model` entry,
  __main__.py) → verified by test_cli_model_subcommand.py additions.

## Chunks

### C1 — Backend: settings + enforcement + defect fixes (implementer 1, Sonnet)
Owned files: `src/localsmartz/global_config.py`, `src/localsmartz/profiles.py`,
`src/localsmartz/agent.py`, `src/localsmartz/pipeline.py`, `src/localsmartz/serve.py`,
`src/localsmartz/runners/__init__.py`, `src/localsmartz/runners/base.py`,
`src/localsmartz/runners/factory.py`, `src/localsmartz/cli/model.py`,
`src/localsmartz/config.py`, `src/localsmartz/doctor.py`, `src/localsmartz/__main__.py`,
`src/localsmartz/secrets.py`, `tests/test_global_config.py`,
`tests/test_agent_config.py`, new test files.
Work: implement frozen contract above; wire active_model into get_model/
get_agent_model/_create_model + pipeline._role_llm; disabled_agents filtering in
_build_subagent_specs, pipeline node builders, list_agents (enabled field),
orchestrator prompt roster line; local_only enforcement at all listed choke
points incl. secrets.export_to_env skip for cloud presets; CLI:
`localsmartz model local-only on|off|status`, `model global <model|clear>`,
`model agents enable|disable <role>`; DEFECT FIXES: (a) execution/agent model
resolution goes through availability check + `resolve_available_model` fallback
with stderr warning (same behavior planning already has), (b) full-profile
default execution model tag corrected to an installed-reality-checked default
(`qwen2.5-coder:32b`), (c) `--check`/doctor validates exact tag presence against
Ollama tags. Tests for every behavior (precedence matrix, enforcement × 3 paths,
disabled-agent filtering, endpoint validation, fallback, check-vs-runtime parity).

### C2 — Prompt improvements via prompt-builder (implementer 2, Sonnet)
Owned files: `src/localsmartz/agents/prompts/*.md` (9 files),
`src/localsmartz/agents/definitions.py` (docstring only if needed).
Work: load Skill("prompt-builder:prompt-builder"); rewrite each prompt calibrated
to SMALL/MID local tier (3-30B): explicit role framing, strict output contracts,
1 compact few-shot example where format-critical (fact_checker, reflector JSON),
tool-name exactness, degradation instructions, token-lean (<450 words each).
Preserve: file names, markdown format (PUT /api/agents/<role>/prompt contract),
and the two FROZEN JSON shapes in the contract section (fact_checker, reflector)
— coded against the frozen contract, no grep of C1-owned files needed.
No orchestration-flow changes.

### C3 — Swift UI (implementer 3, Sonnet; depends on C1 contract only, parallel-safe)
Owned files: `app/LocalSmartz/**` (excluding app/build/**).
Work: (a) General tab: replace free-text active-model field with picker fed by
GET /api/models + "Single model for all agents" semantics; add "Local Only"
toggle; both persist via POST /api/settings ONLY; when the backend is unreachable the
controls render disabled with a "backend offline" caption (no direct global.json
write — Swift must not become a second, validation-bypassing writer of C1-owned
schema keys). (b) AgentsTab: per-agent
enable/disable Toggle wired to POST /api/agents/<name>/enabled; disabled agents
rendered dimmed. (c) PatternTab + EvalTab: when local_only, provider control
shows ollama only (+ caption "Local-Only is on"). (d) AgentRoutingTab: add
deprecation banner pointing to Agents tab (consolidation is a recommendation;
no removal this run). Build check: `xcodebuild -project app/LocalSmartz.xcodeproj
-scheme LocalSmartz -configuration Debug build`.

### Path A vs Path B
- C1 settings write path: Path A = per-key ad-hoc endpoints; Path B (chosen) =
  one `/api/settings` GET/POST contract extensible to future settings keys
  (named future capability: app Settings consolidation, intent §north-star).
- Local-only: Path A = UI-only hiding; Path B (chosen) = typed LocalOnlyError at
  the runner factory boundary so ANY future surface (CLI, new endpoints) inherits
  enforcement.

## Dependency graph
C1 ⊥ C2 (disjoint files). C3 depends on C1's contract (frozen above) — dispatch
in parallel, integration-verify after both land. Comparison harness leg (part 1)
runs concurrently — no file overlap (scratchpad only).

## Approach Lenses
- Clean-sheet lens: a single SettingsStore service + typed provider policy layer.
- Current-constraints lens (chosen): extend existing global_config schema +
  serve.py handler patterns; smallest diff that makes all four capabilities real,
  consistent with the app's existing settings persistence model.

## Risk surface / threat model (riskSurfaceChange: true)
local_only is a privacy/security boundary (blocks data egress to cloud LLM APIs).
Threats considered: (T1) UI-only hiding bypassed via CLI/HTTP → mitigated by
runtime enforcement at all 3 model-construction choke points (deny-by-default
raise, not filter); (T2) stale persisted cloud provider in config.json re-enables
cloud silently → coerced to ollama at read time with a warn event; (T3) secrets
export leaks cloud keys into process env where SDKs auto-discover → export_to_env
skips cloud presets when local_only; (T4) new /api/settings endpoint is
localhost-bound (same trust model as existing endpoints; no auth change).
Residual risk: env vars set outside the app are not scrubbed (documented).
security-reviewer runs at Review-A.

## Parallel decision record
parallel_batch: [C1, C2, C3] — disjoint write-sets (C1 backend py files,
C2 prompts dir, C3 app/LocalSmartz). C3's read-dependency on C1 is satisfied by
the frozen contract above (endpoints + shapes fixed; C3 codes against the
contract, integration-verified after both land). Single-writer git contract:
implementers do not commit; orchestrator commits at choke point.

## Depends-on (reads-from)
- Ollama HTTP API `GET /api/tags` (model availability) — status: verified
  (live, 21 models listed this session).
- `~/.localsmartz/global.json` schema in `global_config.py` — status: verified
  (read this session; agent_models/active_model present).
- `.localsmartz/config.json` project config (planning_model, provider, pattern)
  — status: verified (created by first-run picker during comparison runs).
- serve.py routing table `do_GET/do_POST/do_PUT` (`serve.py:1151-1274`) — status:
  verified (explorer report with line refs).
- DeepAgents subagent spec contract (`_build_subagent_specs` shape) — status:
  verified (agent.py:558-611 read).
- prompt consumers: `pipeline.py` fact_checker verdict JSON, reflector JSON in
  patterns/ — status: unverified (C2 implementer must grep consumers before
  editing; hard requirement in C2 brief).

## MODULARITY note
serve.py is a monolith (3500+ lines); adding /api/settings follows existing
structure — refactor out of scope, recorded as recommendation.

## Verification
- Full pytest suite green (baseline 829-832 passed).
- New precedence/enforcement tests green.
- Live smoke: `localsmartz --check`; serve boot + GET/POST /api/settings round-trip;
  research run with a disabled agent proves filtering (project lesson: live
  HTTP/SSE smoke required when a build touches the runtime server).
- xcodebuild Debug succeeds (app).
- independent-auditor at build scope before final report.

## Success criteria
1. Setting active_model changes the effective model for every role (test + /api/status view).
2. Disabling an agent removes it from subagent specs, pipeline nodes, orchestrator roster (test).
3. local_only=true: cloud selection rejected (403) AND runner construction raises LocalOnlyError (tests × 3 paths).
4. t1/t3 failure class eliminated: full profile resolves an installed execution model with fallback warning (test + re-run).
5. 9 prompts rewritten, JSON contracts intact (grep-verified consumers + tests pass).
6. App builds; new controls function against live backend.
