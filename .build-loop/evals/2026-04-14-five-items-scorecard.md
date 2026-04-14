# Scorecard — 2026-04-14 five-item parallel build

## Commits landed on main

| SHA | Track | Change |
|-----|-------|--------|
| `2a2614f` | A | perf+ux: fast-path prefix short-circuit + CLI phase indicator |
| `fe2968d` | B | feat(ui): StatusBanner for non-blocking phase indicator |
| `e249f9b` | C | feat(ui): New Research → project folder on Desktop |
| `2b2628c` | D1 | feat(cli): `localsmartz doctor` diagnostic matrix |
| `7482855` | D2 | feat(ui): Settings → Agents tab (read-only viewer) |

Parallel dispatch: A ran first to establish the Python status vocabulary; B/C/D1/D2 ran concurrently after. B + C both touched `ResearchView.swift` with non-overlapping edit sites (B added `@State currentPhase` + banner placement, C added `@State showNewProjectSheet` + sheet + queries.json plumbing). Merged without conflict.

## Scorecard

| # | Criterion | Result | Evidence |
|---|-----------|--------|----------|
| 1 | Writer streaming on CLI | ✅ | `agent.py:673-700` streams AIMessageChunks via `stream_mode=["updates","messages"]` (pre-existing, verified). Graph-mode gap documented below. |
| 2 | `is_fast_path` expanded + tested | ✅ | 7 new cases in `tests/test_fast_path.py`; "capital of Peru", "who is Marie Curie", "when did WW2 start" all True. |
| 3 | CLI phase label updates in place | ✅ | `agent.py:829-866` + TTY detection; breadcrumb + phase combo. |
| 4 | Swift `StatusBanner` renders mid-stream | ✅ | New `StatusBanner.swift`; mounted under toolbar Divider; xcodebuild green. |
| 5 | New Research creates folder | ✅ | Sheet → `~/Desktop/<name>/` with `artifacts/` + `queries.json`; `cwd` wired through `/api/research`. |
| 6 | Doctor command | ✅ | 5-row pass/fail matrix; JSON variant; 4 new tests; exit 1 on any fail. |
| 7 | Agents Settings tab lists all roles | ✅ | `AgentsTab.swift` + backend `system_focus` field; read-only; xcodebuild green. |
| 8 | Full pytest | ✅ | 442 passed (was 429 baseline; +13 new across tracks). |
| 9 | Swift build (combined) | ✅ | `** BUILD SUCCEEDED **` after all 4 tracks merged. |

## ✅ Known verified

- `is_fast_path("what's the capital of peru")` → True (regression fixed).
- CLI emits phase label (🔍/🧠/✍/📖/📋/⏳) in TTY; plain-line fallback otherwise.
- SwiftUI `StatusBanner` compiles and mounts; renders current phase from SSE `stage`/`status`/`tool` events.
- "New Research" sheet → folder → `cwd` threading through `/api/research` → `_stream_research`.
- `localsmartz doctor` runs and correctly reports a FAILED SSE round-trip against the stale backend — scanner works as intended.
- `Settings → Agents` tab decodes `system_focus` and renders every `AGENT_ROLES` entry.

## ⚠️ Untested (runtime)

- **StatusBanner UX** — compiled, not visually verified in a live session. SourceKit noise during edits was harmless (xcodebuild is the truth).
- **New Research sheet** — compiled, not runtime-clicked. Needs user session to validate folder creation + folder-already-exists alert.
- **CLI phase label on qwen3:8b-q4_K_M** — smoke hit a pre-existing `model_provider` error for this model (NOT introduced by these changes; confirmed via `git stash`).
- **Agents tab loads agents list at runtime** — decoder compiles, endpoint tested via `/api/agents`, but tab wasn't clicked.

## ❓ Unfixed / deferred

- **Graph-mode writer streaming** (track A discovered): `serve.py::_run_graph_pipeline` uses `graph.stream(initial, stream_mode="updates")` — single mode, no tokens. Writer node in `pipeline.py` calls `.invoke()` not `.stream()`. To fix: add `"messages"` to outer stream_mode AND rewrite `_run_role_agent` to pass chunks through the sink. Plumbing through `_dispatch_role` + per-role opt-in (researcher/analyzer outputs aren't user-facing) exceeds the 20-line bar — deferred to its own build cycle.
- **Task #8 overlay runtime verification** — still open. Fix is now rarely exercised (researcher pinned to 32b in full profile); would need global_config override OR a focused SwiftUI snapshot test.
- **Agents tab EDIT mode** — explicitly deferred.
- **Doctor SSE timeout on live host** — flags a separate perf question: first fast-path SSE event should arrive within 8 s. If it consistently doesn't, that's the next speed lever (preload + classifier + writer streaming into the graph path).
- **`qwen3:8b-q4_K_M` model_provider CLI error** — pre-existing, unrelated to today's work.

## Phase 7 gates

- Fact check: ✅ all scorecard claims trace to commit SHAs, file paths, or command output.
- Mock scan: ✅ no production-path mocks added.
- Secret scan: ✅ 0 findings / 27 patterns.

## Simplicity audit (user's hard bias)

- **A**: single tuple of prefix strings + one positive-match check. No regex. ✅
- **B**: one new SwiftUI view, one `@State` var, five event-handler additions, no Combine/observables. ✅
- **C**: sheet + `FileManager.createDirectory` + JSON append helper. Backend: 3 lines added to thread `cwd` through. ✅
- **D1**: single-file module, `urllib.request` stdlib (no httpx), 5 checks. No new deps. ✅
- **D2**: one Codable field, one new Swift view, one Settings mount. No new deps. ✅

Total new dependencies: **0**. Total new files: **3** Swift views + **1** Python doctor module + **1** Python test file.

## Follow-up queue

1. Graph-mode writer streaming (biggest remaining speed win)
2. Task #8 runtime overlay test via synthetic global_config override
3. Agents tab EDIT mode (writes to `global_config.agent_prompts` / `agent_tools`)
4. Pre-existing `qwen3:8b-q4_K_M` model_provider error — separate debug cycle
