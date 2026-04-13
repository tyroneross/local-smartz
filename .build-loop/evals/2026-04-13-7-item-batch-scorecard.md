# Build-Loop Scorecard — 7-Item UI Batch + Hover Fix (2026-04-13)

## Result: ✅ All 7 batched items shipped + hover regression fixed

| # | Goal | Status | Evidence |
|---|---|---|---|
| Toolbar picker shows model name | Bug fix | ✅ | `modelPickerLabel` has 4 explicit cases; falls back to largest installed model when configured one is missing; persists via `switchModel` |
| Models tab — Install/Remove only | Simplified | ✅ | "Use" button removed; Remove uses `.foregroundStyle(.secondary)` + confirmationDialog (no bright red); RAM-fit chips (Fits/Tight/Too large) on each row |
| Models tab — RAM detection + override | Manual entry | ✅ | "Detected RAM: X GB · Set manually…" banner; `@AppStorage("ramGBOverride")` sheet for user input; effective RAM = override > detected |
| Settings → Telemetry tab | New tab | ✅ | Status (enabled/endpoint/service), toggle for `LOCALSMARTZ_OBSERVE` UserDefault, "Open Phoenix" button → `localhost:6006`, install hint snippet |
| Settings → API Keys tab | Keychain-backed | ✅ | 9 presets (OpenAI/Anthropic/Tavily/Serper/Brave/Exa/Cohere/OpenRouter/LangSmith) + Custom; `keyring` lib (Keychain) with file fallback at `~/.localsmartz/secrets.json` chmod 600; masked display (last 4); Set/Remove inline; backend exports presets to env vars BEFORE tool registration so LangChain auto-picks them up |
| Settings → Debug tab | Logs + feedback | ✅ | In-memory ring buffer (500 lines); auto-poll every 2s; level filter (All/Error/Warn/Info); Send-feedback writes `~/.localsmartz/issues/<ts>_<slug>.md` |
| OutputView — markdown | Native rendering | ✅ | `AttributedString.init(markdown:)` with `.inlineOnlyPreservingWhitespace`; bold/italic/links/inline code render; no SPM dep added |
| **Sidebar hover bug** | UX fix | ✅ | Agent rows replaced with custom HStack + `onTapGesture` + `onHover`; explicit background (selected = accent 10%, hover = secondary 6%, else clear); system Button hover state no longer leaks onto unselected rows |
| CLI updates | New subcommands | ✅ | `localsmartz secrets {list,set,get,delete,export}` and `localsmartz logs {tail,clear}` with `--json` flag |
| Backend startup wiring | Auto-export | ✅ | `secrets.export_to_env()` called before `start_server()`; logs how many keys exported via `log_buffer.info("secrets", ...)` |

## Architecture (NavGator scan)

- **Before**: 11 components, 0 connections, 1 AI provider
- **After**: 18 components, 47 connections, 3 AI providers
- New backend modules surfaced: `secrets`, `log_buffer`, `observability`, `plugins/*`, `agent_integration`
- New external integrations: OpenTelemetry (api/sdk/exporter), OpenInference instrumentation, keyring

## Tests

- `pytest tests/` → **304 passed** (13 new: 8 secrets + 5 log_buffer; 0 regressions)

## Files added (this round)

- `src/localsmartz/secrets.py` — Keychain + file fallback storage
- `src/localsmartz/log_buffer.py` — in-memory ring buffer
- `app/LocalSmartz/Views/SettingsTabs.swift` — Telemetry, API Keys, Debug tabs (~670 LoC)
- `tests/test_secrets.py`, `tests/test_log_buffer.py`

## Files modified

- `pyproject.toml` — added `keyring>=24.0.0`
- `src/localsmartz/__main__.py` — `secrets` and `logs` noun subcommands; `secrets.export_to_env()` at startup
- `src/localsmartz/serve.py` — 6 new endpoints (`/api/secrets` GET/POST/DELETE, `/api/logs` GET/DELETE, `/api/issues/report` POST)
- `src/localsmartz/secrets.py` — `set` shadows `builtins.set` collision fixed in `masked_all`
- `app/LocalSmartz/Views/SettingsView.swift` — 8-tab TabView, frame bumped to 640x520
- `app/LocalSmartz/Views/ResearchView.swift` — picker bug fix, fallback to largest installed
- `app/LocalSmartz/Views/ModelsTab.swift` — Install/Remove only, RAM banner + chips
- `app/LocalSmartz/Views/OutputView.swift` — markdown rendering
- `app/LocalSmartz/Views/ThreadListView.swift` — AgentRow extracted to fix hover bug

## DMG

- 93 MB (up from 89 MB; +keyring +OTel deps + new tabs)
- Installed to `/Applications/Local Smartz.app`
- Verified live: backend spawns on port 11435, all new endpoints respond, IBR scan: 68 elements, 0 console errors

## Parallel execution

- 3 subagents dispatched in parallel for backend/SettingsView/ResearchView+Models+Output
- Foundation files (secrets.py + log_buffer.py) written by main thread first
- All 3 agents reported BUILD SUCCEEDED + tests green
- Main thread fixed 1 cross-agent collision (secrets.set shadowing) + hover bug found in screenshot

## Verified via IBR scan_macos

Final scan: dashboard listing, 68 elements (10 interactive), 9 buttons, all selectors functional. False-positive accessibility warnings on macOS traffic-light buttons (system-rendered, not addressable).

## Known follow-ups (deferred to next iteration)

- **Calm Precision deep pass** still queued (#41) — only toolbar pass done
- Block-level markdown (lists, headings, tables) still inline-only; future SPM integration with swift-markdown-ui
- Code blocks don't have syntax highlighting yet
- Artifact cards (file outputs) not yet rendered as cards — still plain text in OutputView
- Telemetry toggle requires app restart to take effect (env-var capture happens in spawned backend startup)
- Custom API key providers don't auto-export to env (no canonical env var name) — only presets
