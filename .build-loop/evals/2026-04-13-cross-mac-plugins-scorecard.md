# Build-Loop Scorecard — 2026-04-13

Project: local-smartz · Branch: `main` · Base: `a243bf6`

## Result: ✅ All 5 criteria passed (no iteration needed)

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Plugin/skill/MCP system functional | ✅ | `pytest` — 32 plugin tests + 11 agent-integration tests + 21 CLI tests pass; `plugins install bookmark` against the real RossLabs-AI-Toolkit succeeds; validator emits error codes (`MISSING_NAME`, `INVALID_SEMVER`, `MALFORMED_YAML`, `MISSING_FRONTMATTER`, …) with file path |
| 2 | Concurrent HTTP server | ✅ | `tests/test_serve_concurrency.py` 2/2 pass; 5 parallel requests (200ms each) finish in <400ms; `start_server()` constructs `ThreadingHTTPServer` |
| 3 | Native Setup view + Settings surface | ✅ Compile-verified | `xcodebuild -scheme LocalSmartz -configuration Debug` → BUILD SUCCEEDED; `SetupView` rewritten to spawn temp backend, call `/api/status`, stream `/api/setup` SSE; `SettingsView` + `GlobalSettings.swift` present, atomic write to `~/.localsmartz/global.json` |
| 4 | CI + cross-Mac install | ✅ | `.github/workflows/python.yml` + `.github/workflows/macos.yml` valid YAML, all actions pinned to majors; `install.sh` uses `set -euo pipefail`, `--no-ollama` flag, `[ -t 0 ]` interactive guard, `$HOME` not hardcoded paths; `pyproject.toml` license now `{file = "LICENSE"}` matching FSL-1.1-MIT |
| 5 | No regressions + scanners clean | ✅ | Full suite **256/256 passed** in 5–25s; Gate B (mock scan) **0 blocking**; Gate C (secret scan) **0 hits across 27 patterns** |

## Files added/modified

### New (16 files)
- `src/localsmartz/global_config.py`
- `src/localsmartz/plugins/{__init__,discovery,validator,loader,registry,mcp_client,tools_adapter,agent_integration}.py` (8 files)
- `tests/test_global_config.py`, `test_plugins_validator.py`, `test_plugins_registry.py`, `test_plugins_mcp_client.py`, `test_agent_plugin_integration.py`, `test_cli_subcommands.py`, `test_serve_concurrency.py`, `tests/fixtures/mock_mcp_server.py`
- `app/LocalSmartz/Views/SettingsView.swift`, `SetupProgressView.swift`
- `app/LocalSmartz/Models/GlobalSettings.swift`
- `app/LocalSmartz/Services/SetupSSEClient.swift`
- `app/scripts/embed-python.sh`, `app/scripts/README.md`
- `.github/workflows/python.yml`, `.github/workflows/macos.yml`, `.github/workflows/README.md`

### Modified
- `src/localsmartz/agent.py` — `create_agent()` returns 4-tuple `(agent, profile, checkpointer, mcp_clients)`; `include_plugin_skills`/`include_plugin_tools`/`include_mcp` kwargs; system prompt assembled via `_build_system_prompt()`
- `src/localsmartz/__main__.py` — noun-based subcommand dispatcher (`plugins`/`skills`/`config`); fixed pre-existing `create_agent(profile)` call
- `src/localsmartz/serve.py` — `ThreadingHTTPServer` + `daemon_threads = True`; updated `create_agent()` 4-tuple unpack; MCP cleanup at end of `_handle_research_request`
- `app/LocalSmartz/Views/SetupView.swift` — full rewrite (288 → 604 lines)
- `app/LocalSmartz/LocalSmartzApp.swift` — `Settings` scene wired
- `app/LocalSmartz/Services/BackendManager.swift` — `resolvePythonPath()` prefers bundled Python
- `app/build-dmg.sh` — embeds python-build-standalone into `.app/Contents/Resources/python/`
- `install.sh` — `--no-ollama`, `--help`, hardened ordering, `[ -t 0 ]` interactive guard
- `README.md` — install section + new "Plugins & Skills" section + "Cross-Mac install" subsection
- `pyproject.toml` — license fixed to `{file = "LICENSE"}`

## Known caveats (not blocking, flagged for follow-up)

1. **Bundled Python SHA256 placeholders** — `app/scripts/embed-python.sh` ships `PLACEHOLDER_REPLACE_WITH_REAL_SHA256_*` for the python-build-standalone tarballs. Script warns on execution and skips verification. Real hashes must be pasted before distribution. (Sandbox blocked WebFetch.)
2. **README `<repo>` placeholder** — install URL has `<repo>` placeholder + TODO comment. Replace with real GitHub `owner/repo` before user-facing publish.
3. **Plugin command tools are guidance-only (v1)** — invocation returns the command's markdown body to the model; no shell exec yet. Documented in `tools_adapter.commands_to_tools()` docstring. True sandboxed exec deferred.
4. **MCP integration is opt-in** (`create_agent(include_mcp=True)`); default `False`. Server-initiated requests, capability negotiation beyond `initialize`, and progress streams not implemented in the minimal stdio client.
5. **`/usr/bin/env python3` fallback in `BackendManager.resolvePythonPath()`** — Foundation's `Process` doesn't PATH-resolve; bundled Python is the dominant path so the fallback rarely fires. Pre-existing concern.
6. **CI ignores `test_ollama.py` and `test_serve.py`** — these need a running Ollama. Documented in `.github/workflows/README.md`.
7. **No Developer ID signing/notarization** — `app/build-dmg.sh` does ad-hoc `codesign --sign -`. Distribution requires real Apple credentials.

## Verification commands run
- `pytest tests/ --ignore=tests/test_ollama.py --ignore=tests/test_serve.py` — 236/236 ✅
- `pytest tests/test_serve.py` — 20/20 ✅
- `xcodegen generate` (in `app/`) — ok
- `xcodebuild -scheme LocalSmartz -configuration Debug build` — BUILD SUCCEEDED ✅
- `bash -n install.sh app/build-dmg.sh app/scripts/embed-python.sh` — clean
- `node audit-hardcoded-secrets.mjs` — 0 hits across 27 patterns ✅
- mock-scanner subagent — 0 blocking
