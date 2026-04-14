# Scorecard — 2026-04-14 three-item polish

## Commits landed on main

| SHA | Scope |
|-----|-------|
| `0b286b4` | fix(agent): strip with_retry from _create_model |
| `87d3d89` | perf(profiles): pin full.researcher to 32B |
| `ca04df1` | fix(ui): don't cover OutputView mid-stream |
| `56a2df3` | chore(deps): rename duckduckgo-search to ddgs |

## Scorecard

| # | Criterion | Result | Evidence |
|---|-----------|--------|----------|
| 1 | Researcher pinned to 32B in full profile | ✅ | `profiles.py` researcher["model"] = qwen2.5-coder:32b |
| 2 | Full pytest suite green | ✅ | `uv run pytest -q` → 429 passed |
| 3 | Warmup overlay suppressed mid-stream | ✅ | `ResearchView.swift:107` guarded on `!isStreaming` |
| 4 | Swift app compiles | ✅ | `xcodebuild -scheme LocalSmartz -configuration Debug` → BUILD SUCCEEDED |
| 5 | `ddgs` import works | ✅ | `from ddgs import DDGS` → OK |
| 6 | `duckduckgo_search` absent from code | ✅ | grep exit=1 |
| 7 | Web-search tests green | ✅ | 18/18 in test_tools.py |

## ✅ Known verified

- Researcher/analyzer/fact_checker/writer all run qwen2.5-coder:32b — no mid-round VRAM swap in the graph pipeline on full profile.
- Overlay gate means in-flight streamed content stays visible on mid-stream specialist swaps; a tool-call breadcrumb surfaces the load event.
- ddgs 9.13.1 installed, `from ddgs import DDGS` works, lockfile regenerated.
- RunnableRetry unhashable crash in focus mode fixed at its root (`_create_model`).

## ⚠️ Untested

- End-to-end runtime verification of the overlay fix — needs a live swap to trigger `status(stage="loading_model")` mid-stream. Now unlikely in full profile (all graph roles share model); would need a focus-mode swap or lite-profile scenario to exercise.
- ddgs runtime behavior (rate limits, HTML-parsing shifts). Mock test doesn't catch these.

## ❓ Unfixed

None — all three items from the bookmark addressed.

## Phase 7 gates

- Fact check: ✅ all claims trace to code/tool output/T1 source (PyPI).
- Mock scan: ✅ no production-path mocks added.
- Secret scan: ✅ 0/27 patterns.

## Out-of-scope items noted but deferred

- Tracing wiring default-on for `--dev` (opt-in Phoenix already exists via `LOCALSMARTZ_OBSERVE=1`).
- LangSmith API-key distribution (still requires user-supplied `.env`).
