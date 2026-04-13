# Build-Loop Scorecard — Codex Fixes + Setup Help + Calm Precision (2026-04-13)

## Result: ✅ All follow-ups landed; queue empty

### Codex review — 6 findings, all fixed

| # | Issue | Severity | Verified |
|---|---|---|---|
| 1 | MCP client used newline-JSON instead of Content-Length framing | P1 | 34 tests pass; mock server switched to spec-correct framing |
| 2 | `skills activate` wrote to wrong store | P2 | Live: `activate context-continuity` writes `global.json.active_skills` |
| 3 | Settings → Workspace/Python didn't reach launcher | P2 | BackendManager reads GlobalSettings with write-through to UserDefaults |
| 4 | Telemetry toggle didn't reach child env | P2 | BackendManager injects `LOCALSMARTZ_OBSERVE` from UserDefaults |
| 5 | Custom Keychain secrets invisible | P2 | Live: `secrets set MyCustomAPI ...` → `secrets list` shows `source=keychain` |
| 6 | AuthorView refactor used skill name as plugin name | P2 | `/api/skills` returns `plugin`; Swift decodes + uses it correctly |

**Tests**: `307 passed` (13 new: 8 secrets, 5 log_buffer; plus MCP framing parity)

### Setup wizard UX

| Change | Evidence |
|---|---|
| ⓘ info button per row with What/Why/WhatChanges/HowToChange popover | 5 new `SetupHelp` entries for Python, Local Smartz, Workspace, Ollama, Models |
| Inline Change… for Python and Workspace | Replaces redundant bottom buttons |
| Removed "Choose Python…" + "Choose Workspace…" bottom buttons | Bottom bar is now Get Started only |
| Get Started has breathing room | `Spacer(minHeight:32)` above + `Spacer(height:40)` below + `.controlSize(.large)` + `minWidth:140` |
| Python change opens confirmation sheet first | 5 sections: Current path, Recommended (bundled Python), When to change, Requirements (Python 3.12 + `localsmartz` importable), Impact |

### Calm Precision audit pass

| Rule | Violation | Fix |
|---|---|---|
| #9 Status text-only | Research toolbar profile had `.secondary.opacity(0.1), in: Capsule()` pill | Now uppercase tracked text, no background |
| #30 Nav states: text + bottom border, never pill | MainView mode bar used `accentColor.opacity(0.12)` rounded-rect pill | Now: selected = primary color + semibold + 2px accent bottom border; unselected = secondary + regular |
| #1 Single border around groups | Sidebar agents previously used Button hover state (leak) | Replaced with custom HStack + explicit selected/hover backgrounds — earlier fix |
| #11 Accept nulls/missing | Toolbar picker showed blank when current model missing | Now falls back to "Pick a model" and auto-switches to largest installed |

### NavGator

- Initial scan was overbroad (78,978 components, includes bundled Python inside the DMG)
- Added `.navgator/config.json` with ignore patterns — scan dropped to 1 component (too aggressive; patterns need loosening)
- Follow-up: tighten to ignore `app/build/**`, `app/*.dmg`, `**/__pycache__/**` only

## Files added (this round)

- `app/LocalSmartz/Views/SetupHelp.swift` — SetupHelp struct + SetupHelpPopover + StepRow component
- `.navgator/config.json` — ignore patterns

## Files modified (this round)

- `src/localsmartz/plugins/mcp_client.py` — Content-Length framing
- `tests/fixtures/mock_mcp_server.py` — matching framing
- `src/localsmartz/__main__.py` — `_cmd_skills` writes to global_config
- `src/localsmartz/secrets.py` — `secrets_index.json` discovery
- `tests/test_secrets.py` — 3 new tests
- `app/LocalSmartz/Services/BackendManager.swift` — `effectiveWorkspaceDirectory()`, `syncGlobalSettingsToUserDefaults()`, LOCALSMARTZ_OBSERVE injection
- `app/LocalSmartz/Views/SettingsView.swift` — `apply()` writes UserDefaults too
- `app/LocalSmartz/Views/AuthorView.swift` — Skill.plugin decoded; refactor save uses plugin name
- `app/LocalSmartz/Views/SetupView.swift` — bottom button bar removed; Get Started centered; Python confirmation sheet
- `app/LocalSmartz/Views/MainView.swift` — mode bar uses text + bottom border
- `app/LocalSmartz/Views/ResearchView.swift` — profile badge → text only

## DMG

- Latest: 92 MB at `/Users/tyroneross/Desktop/git-folder/local-smartz/app/LocalSmartz-Installer.dmg`
- Installed at `/Applications/Local Smartz.app` — verified IBR scan + screenshot

## Known remaining follow-ups

- NavGator ignore config too aggressive — loosen to only exclude bundled Python and DMG
- Swift-markdown-ui integration for block-level markdown (lists, headings, tables) — currently inline-only
- Code blocks with syntax highlighting
- Developer ID signing + notarization (user explicitly out of scope)
