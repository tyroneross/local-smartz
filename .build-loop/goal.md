# Build-Loop Goal — local-smartz cross-Mac + plugin/skill capable

Date: 2026-04-13
Branch: main (`a243bf6`)
Project: local-smartz (Python 3.12 + SwiftUI macOS app, Ollama-backed local LLM agent)

## Goal

Make local-smartz: (a) usable identically via CLI and Mac app, (b) installable on any Mac with local LLMs, (c) capable of loading and validating plugins/skills/MCP servers from `~/Desktop/git-folder/RossLabs-AI-Toolkit` (default) into the local-LLM agent. Add native model setup, concurrent HTTP serving, settings parity, tighter packaging, and CI.

## Deliverables

1. `src/localsmartz/plugins/` Python package: discovery, validator, registry, loader for plugin.json + SKILL.md + commands + hooks + .mcp.json
2. CLI subcommands: `localsmartz plugins {install,list,validate,remove}`, `localsmartz skills {activate,deactivate,list}`, `localsmartz config {get,set}`
3. Skills inject into ChatOllama system prompt; plugin commands become invocable tools; MCP servers spawn and expose tools
4. `serve.py` uses `ThreadingHTTPServer` instead of single-threaded `HTTPServer`
5. SwiftUI `SetupView` shows missing models with progress bars driven by `/api/setup` SSE
6. SwiftUI Settings scene + matching CLI for: workspace, python_path, active_model, plugin_paths
7. `app/build-dmg.sh` produces an `.app` bundle that does not require external Python (bundled venv or python-build-standalone)
8. `.github/workflows/python.yml` + `.github/workflows/macos.yml` running on PR
9. `install.sh` + README updated for one-command install on a fresh Mac

## Scoring Criteria (5 binary pass/fail)

| # | Criterion | Method | Pass condition | Evidence |
|---|---|---|---|---|
| 1 | Plugin/skill/MCP system functional | code: pytest + CLI smoke | `pytest tests/test_plugins*.py` green AND `localsmartz plugins install bookmark` succeeds against real toolkit AND malformed entry produces actionable error AND a sample skill body appears in agent system prompt | test output + CLI session |
| 2 | Concurrent HTTP server | code: pytest concurrency test | New test fires 5 simultaneous `/api/health` requests and verifies parallel handling (total time < 2x single-request latency) | test output |
| 3 | Native Setup view + Settings surface | LLM judge (binary) | SwiftUI files present (SetupView.swift updated, SettingsView.swift new), reference `/api/setup` and `/api/status`, settings persists via shared config; CLI `localsmartz config get/set` works | judge reasoning + file diffs + CLI run |
| 4 | CI + cross-Mac install | code: file existence + lint | `.github/workflows/{python,macos}.yml` valid YAML, `actionlint` clean (or skipped if not installed), install.sh uses no hardcoded user paths, `pyproject.toml` declares all deps with version pins | actionlint output + grep for `/Users/`, pyproject inspection |
| 5 | No regressions + secrets/mocks clean | code: full test suite + scanners | `pytest tests/` green (existing 12 files), Gate B (mock scan) clean for production paths, Gate C (secret scan) clean | test output + scanner reports |

## Out of Scope (this loop)

- Code signing / notarization with real Apple credentials (stub only if creds absent)
- Plugin marketplace / remote install
- Web UI redesign
- Multi-LLM backend abstraction beyond Ollama (skills can be authored to mention other backends, but agent layer stays Ollama)

## Risks

- **MCP client implementation in Python is non-trivial** — using `mcp` Python SDK if available; otherwise custom stdio JSON-RPC client. Risk: scope creep.
- **Bundling Python in .app is fragile** — `python-build-standalone` is the lowest-friction route; risk that signing/notarization breaks dynamic libs.
- **Plugin command `allowed-tools` enforcement requires sandbox** — initial implementation will be opt-in with a clear "untrusted" warning, not a true sandbox.
- **Existing tests may not cover new code paths well** — Phase 6 may iterate on coverage gaps.
