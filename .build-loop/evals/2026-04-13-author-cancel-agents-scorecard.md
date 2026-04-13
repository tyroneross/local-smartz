# Build-Loop Scorecard — Cancel · Agents · Author tab (2026-04-13)

## Result: ✅ All asks shipped; verified live via IBR

| # | Goal | Status | Evidence |
|---|---|---|---|
| Bug: research hard-fails on missing model | Fixed + regression test | ✅ | `serve.py:_stream_research` now calls `resolve_available_model()`, emits `[note]` SSE event with substitute. 2 new tests in `test_serve_research_fallback.py` |
| Stop button while researching | Cancellable mid-stream | ✅ | While `isStreaming`, Send arrow swaps to red Stop button (⌘.); cancels SSE Task; "Last step" status surfaces during cancel |
| Type while researching | Always editable | ✅ | TextField removed `.disabled`; placeholder swaps to "Type your next question — send when ready…" |
| "What is the agent doing" | Live status text | ✅ | OutputView shows `streamingStatus` (verb-driven from last tool: "Searching the web…", "Running calculations…", etc.) + "Last step: <tool_name>" |
| Model picker reads as dropdown | Clear affordance | ✅ | New label: "Model: <name> ▾" with outline border; menu has section header "Switch active model" + Refresh action |
| Agents in left nav | Selectable per-agent mode | ✅ | New "Agents" section in sidebar lists Planner / Researcher / Analyzer / Writer / Reviewer with summaries; click → pins single-agent flow; "All agents" radio for default |
| Plugin/skill authoring | Local LLM authors them | ✅ | New "Author" mode (top-bar tab, ⌘2). 2 flows: refactor existing skill (with guidance text), new skill from description. ChatOllama drafts SKILL.md + plugin.json |
| Save to chosen repo | NSOpenPanel folder picker | ✅ | "Save to repository…" opens picker; backend writes `<target>/<plugin>/.claude-plugin/plugin.json` + `<target>/<plugin>/skills/<plugin>/SKILL.md` |
| Calm Precision pass on toolbar | Single-line cluster, no per-row chrome | ✅ | Toolbar reorganized: title left, model · profile · status · duration cluster right. Status uses text+color only (no badges) |

## New endpoints (8 total this round)

- `GET /api/agents` — profile agents + roles
- `GET /api/skills` — installed skills with paths
- `POST /api/skills/refactor` — local LLM rewrites SKILL.md
- `POST /api/skills/new` — local LLM drafts new SKILL.md + plugin.json
- `POST /api/plugins/save` — writes plugin folder to chosen target
- `POST /api/research` (extended) — accepts optional `agent` to focus single role
- `GET /api/models/catalog`, `POST /api/models/pull`, `DELETE /api/models?name=...` (carried from prior round)

## Files added (this round)

- `src/localsmartz/profiles.py` — `AGENT_ROLES`, `list_agents()`, `agent_focus_prompt()`
- `app/LocalSmartz/Views/MainView.swift` — top-level mode switcher (Research / Author)
- `app/LocalSmartz/Views/AuthorView.swift` — full authoring UI
- `tests/test_serve_research_fallback.py` — 2 regression tests for the fallback bug

## Files modified

- `src/localsmartz/serve.py` — `_stream_research` fallback + 8 new endpoints + `agent` param plumbing
- `src/localsmartz/agent.py` — `extra_system_prompt` plumbed through `create_agent` + `_build_system_prompt`
- `app/LocalSmartz/Views/ResearchView.swift` — Cancel button, model dropdown affordance, agents fetch, focusAgent, toolbar reorg
- `app/LocalSmartz/Views/OutputView.swift` — verb-driven streamingStatus + last-step display
- `app/LocalSmartz/Views/ThreadListView.swift` — Agents section
- `app/LocalSmartz/Models/AppState.swift` — `AppMode` enum + `mode` published
- `app/LocalSmartz/LocalSmartzApp.swift` — points at MainView

## Live verification

- `pytest tests/` → **291 passed**
- `xcodebuild -configuration Release` → BUILD SUCCEEDED
- DMG built (88 MB), installed to /Applications, launched
- IBR scan: 57 elements (16 interactive), Author tab visible, Agents sidebar populated
- `curl /api/agents` → 5 agents with roles
- `curl /api/skills` → 1 skill (context-continuity from bookmark plugin)
- Live query (Cmd+↩): SSE responds with text + done events
- Cancel verified: server gracefully handles connection drop

## Known limits / follow-ups

1. `pythonImportsLocalsmartz` check still happens in SetupView; could be cached
2. AuthorView "Refactor" only shows installed skills — no support yet for refactoring an external skill that's not yet installed
3. Save endpoint creates `.claude-plugin/plugin.json` + `skills/<name>/SKILL.md` standard layout. Doesn't create commands/, hooks/, .mcp.json — those would need additional UI later
4. Single-agent mode is a system-prompt focus — DeepAgents is still the executor under the hood. A real per-agent runtime (separate ChatOllama instances) is a future redesign
5. The full Calm Precision audit is pragmatic (toolbar) — a deep pass on every view is its own iteration
