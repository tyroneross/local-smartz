# Scorecard — 2026-04-14 Track B (Codex triage) + Track C (project sidebar)

## Commits landed on main

| SHA | Track | Change |
|-----|-------|--------|
| `a3f49cb` | B1 | fix(agent): normalize subagent model strings for DeepAgents compatibility |
| `5d7ea5b` | B3+B4 | feat(profiles): tighten analyzer + fact-checker contracts per audit |
| `1ff7fa4` | C | feat(ui): project folders in sidebar — expandable, clickable, deletable |

## Scorecard

| # | Criterion | Result | Evidence |
|---|-----------|--------|----------|
| 1 | CLI crash fixed (Finding 1) | ✅ | `uv run localsmartz --quiet "what is 2 plus 2"` → "2 plus 2 equals 4." No `ValueError(model_provider)`. |
| 2 | Full pytest green | ✅ | 450 passed (was 442; +8 new tests) |
| 3 | Analyzer prompt + downstream labels fixed (Finding 3) | ✅ | `grep -n '"plan"' src/localsmartz/pipeline.py` now zero hits; analyzer contract narrowed |
| 4 | fact_checker has scrape_url (Finding 4) | ✅ | `profiles.py` tools list updated; prompt directives added to researcher + fact_checker |
| 5 | ProjectIndex round-trips | ✅ | `ProjectIndex.swift` loads, reconciles with disk, writes back; add/remove implemented |
| 6 | Sidebar Projects section | ✅ | `ThreadListView.swift` renders new "Projects" section above "Agents" |
| 7 | Click saved query loads into OutputView | ✅ | `ResearchView` loads answer + shows "Viewing saved query" banner; input disables |
| 8 | Delete project removes folder + index | ✅ | Confirm alert → `FileManager.removeItem` + `projectIndex.remove` |
| 9 | xcodebuild green | ✅ | `** BUILD SUCCEEDED **` after all 3 commits |

## ✅ Known verified

- CLI end-to-end works with qwen3:8b-q4_K_M via fast-path (via gpt-oss:120b per user's `activeModel`; no provider inference crash).
- Analyzer role no longer contradicts graph topology.
- Fact-checker can now actually scrape claims (previously snippet-only).
- Project index persists, reconciles with disk, filters dead entries silently.
- Sidebar compiles, xcodebuild green; new Swift file (`ProjectIndex.swift`, 97 lines) registered via xcodegen.

## ⚠️ Untested / Runtime-deferred

- **Sidebar visual runtime test** — code-verified + built; not runtime-exercised via IBR. Smoke harness needs the Get Started AX-path regression fix first (smoke v4 from earlier).
- **Writer streaming (Codex Finding 2)** — explicitly out of scope this cycle. Biggest remaining speed win.
- **Queries.json live reload** — `projectQueriesCache` in `ThreadListView` doesn't invalidate when a fresh query is appended during the same session. Agent flagged this; minor UX nit. Fix: invalidate cache[projectDir] in `.done` handler. 3-line change for next cycle.
- **Saved-answer fidelity** — `appendQueryRecord` persists only the first 240 chars. Saved-query view shows that preview. Fold full-answer persistence into Finding 2 (writer streaming) follow-up cycle.

## ❓ Unfixed / deferred

- **Codex Finding 2** (writer streaming) — concrete plan documented, deferred to next cycle
- **Codex Finding 5** (file-handoff prompts) — nice-to-have, low priority
- **Secondary preflight finding** (truncated in Codex task log) — re-run `/codex:rescue` with tighter scope to capture the full thought
- **Smoke harness Get Started AX-path regression** — 1-line fix (switch to coord-click), blocks full runtime verification

## Phase 7 gates

- Fact check: ✅ all scorecard claims trace to commit SHAs, command output, or code paths
- Mock scan: ✅ no production-path mocks added
- Secret scan: ✅ 0 findings / 27 patterns

## Simplicity audit

- **B1**: wrap bare model in `_create_model()`. 12 lines changed. ✅
- **B3**: prompt rewrite + 2 label strings renamed. 7 lines changed in pipeline. ✅
- **B4**: +1 tool entry, 2 prompt append strings. 14 lines in profiles. ✅
- **C**: 1 new Swift file (97 lines), 3 existing files edited (banner + closure wiring). Zero Combine, zero new deps. ✅

Total new files: **1 Swift** (`ProjectIndex.swift`)
Total new tests: **+8 pytest** (subagent normalization + profile regression)
New dependencies: **0**

## Follow-up queue

1. Codex Finding 2 — writer streaming in graph mode (biggest speed win)
2. Smoke harness Get Started coord-click fix + runtime verify sidebar
3. Cache invalidation for live-reload of queries in expanded projects
4. Full-answer persistence in `appendQueryRecord` (ties to #1)
5. Re-run `/codex:rescue` to capture the truncated "server preflight" finding
