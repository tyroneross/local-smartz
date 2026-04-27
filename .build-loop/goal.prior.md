# Goal: Codex-audit triage (B) + Project sidebar (C) — parallel

Date: 2026-04-14 (supersedes prior goal)
Branch: main
Base state: 3 commits ahead of origin. 442/442 pytest. Swift build clean.

## Track B — Codex audit triage (Python)

### B1. Normalize subagent model strings (BLOCKER — fixes CLI crash)

**Root cause** (Codex): `_build_subagent_specs()` in `src/localsmartz/agent.py:398` passes bare Ollama model strings (`qwen3:8b-q4_K_M`) into DeepAgents, which resolves them via LangChain's `chat_models.base.py` — LangChain can't infer a provider from a bare name and raises `ValueError`.

**Fix**: for every model handed to DeepAgents, either
- (a) wrap in a prebuilt `ChatOllama` via `_create_model()` and pass the instance, OR
- (b) prefix with `ollama:<model>` string

Prefer (a) for consistency with the RunnableRetry rule we already enforce (no wrap before `bind_tools`). Regression test that reproduces the `ValueError` without the fix.

### B3. Analyzer prompt/topology fix (IMPORTANT)

**Mismatch**: Prompt (`profiles.py:53`) says "read prior research from disk and compute real values," but the graph runs researcher+analyzer in parallel (`pipeline.py:433`); analyzer gets only the original query. Output then labeled "plan" downstream (`pipeline.py:398`, `421`) — contradicts the prompt.

**Fix**: narrow the analyzer contract to prompt-only + local-file computation. Remove the "read prior research" directive. Rename downstream state key from "plan" / "analyzer plan" to "analysis" / "computed values." Don't reorder the graph — keeping parallel execution is the speed win.

### B4. Evidence contract tightening (IMPORTANT)

Current: `web_search` returns only snippets (`tools/web.py:227`). Researcher has `scrape_url` but no guard forces scrape before synthesis. `fact_checker` lacks `scrape_url` entirely (`profiles.py:86`), so spot verification only reads snippets.

**Fix**:
- Add `scrape_url` to `fact_checker` tools allow-list
- Update researcher prompt: "Do not cite a claim based on a search snippet alone — scrape at least one URL before treating a finding as confirmed."
- Update fact_checker prompt: "When a claim is doubtful, scrape the best URL from prior research before issuing a verdict."

## Track C — Project folders sidebar (Swift + minimal backend)

### C1. Project index persistence

**Schema**: `~/.localsmartz/projects.json`:
```json
{ "projects": [ { "name": "Peru facts", "path": "/Users/t/Desktop/peru-facts", "createdAt": "2026-04-14T19:00:00Z" } ] }
```

**Operations**:
- Read on app launch; silently skip entries whose path no longer exists
- Append on "Create" in the New Research sheet
- Remove on delete

### C2. Sidebar UI

Extend `ThreadListView.swift`:
- New "Projects" section above "Agents", above threads
- Each project = `DisclosureGroup` with name header
- Expanded: list of queries from that project's `queries.json` (compact — one line each: truncated question + timestamp)
- Click a query → emits a callback with the query payload (delegated via closure to `ResearchView`)
- Context menu on project: "Delete project" → confirm alert → `FileManager.removeItem` + index update

### C3. Load query into OutputView

When user clicks a saved query, load its question + answer into the main content area, read-only. No tool-call reconstruction (those aren't persisted in queries.json). Shows:
- Title row: the question
- Content: the answer text
- Muted banner: "Viewing saved query from <project> · <timestamp>"
- Standard input bar reappears for new queries; clicking New Research or another project resets.

### C4. (deferred) Re-run button on saved queries — explicitly out of scope

## Scoring Criteria

| # | Criterion | Method | Pass | Evidence |
|---|-----------|--------|------|----------|
| 1 | CLI crash fixed | pytest regression | `test_build_subagent_specs_normalizes_model` passes; without fix it fails with `ValueError` | pytest output |
| 2 | Full pytest green | `uv run pytest -q` | 442+ (new tests added) | pytest output |
| 3 | Analyzer prompt updated; downstream labels renamed | grep | new prompt present; "plan" → "analysis"/"computed values" in pipeline.py | diff |
| 4 | fact_checker has scrape_url | code | `profiles.py:86` entry contains scrape_url; smoke import works | diff |
| 5 | Project index file round-trips | Swift or Python unit | new project appended + reloaded correctly | unit test OR runtime scan |
| 6 | Sidebar Projects section renders | xcodebuild + IBR scan | new section visible with expandable row after creating project | screenshot |
| 7 | Click saved query loads into OutputView | IBR scan or manual | question + answer render; "Viewing saved query" banner visible | screenshot |
| 8 | Delete project removes folder + index | manual | confirm dialog → folder gone from Desktop + gone from sidebar | shell + UI check |
| 9 | xcodebuild green | xcodebuild | BUILD SUCCEEDED | build log |

## Parallelization

- **Track B**: pure Python (`agent.py`, `profiles.py`, pipeline.py rename, test files)
- **Track C**: pure Swift (`ResearchView.swift`, `ThreadListView.swift`, new `ProjectIndex.swift`) + tiny backend read if needed

Zero file overlap. Fully parallel — dispatch as two subagents simultaneously.

## Constraints

- Zero new deps
- Simplicity first — simple > clever if perf+reliability equal
- Preserve today's RunnableRetry discipline (no `.with_retry()` before `create_agent` / `create_deep_agent`)
- Small reversible commits

## Out of scope (deferred)

- Codex Finding 2 (writer streaming in graph mode) — separate cycle
- Codex Finding 5 (file-handoff prompt cleanup) — nice-to-have, fold into B later
- The secondary Codex finding about server preflight warmup — surface area unclear, revisit after re-running rescue with tighter scope
- Re-run button on saved queries (C4 out of scope)
- Pre-existing smoke harness Get Started AX-path regression (fixable with 1-line coord-click change; do after these commits)
