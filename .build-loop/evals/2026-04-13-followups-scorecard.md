# Build-Loop Scorecard — Follow-ups Iteration (2026-04-13)

Project: local-smartz · Branch: `main`

## Result: ✅ All targets met; install actually works now

| # | Goal | Status | Evidence |
|---|---|---|---|
| 1 | PBS SHA256 pinning | ✅ | Real hashes from `cpython-3.12.5+20240814-{aarch64,x86_64}-apple-darwin-install_only.tar.gz.sha256` pinned; placeholder bypass branch removed; `bash -n` clean |
| 2 | README repo placeholder | ✅ | `<repo>` → `tyroneross/local-smartz` |
| 3 | Plugin commands as real exec | ✅ | 14 new tests in `tests/test_plugins_exec.py`; subprocess + 30s timeout + stripped env (`HOME PATH USER LANG LC_ALL TERM SHELL TMPDIR` only) + workspace cwd; falls back to guidance when `Bash` not in `allowed-tools` |
| 4 | Install actually works | ✅ | `uv tool install --reinstall .` → `localsmartz --check` succeeds, falls back to `gpt-oss:120b` when configured `llama3.1:70b` is missing; `plugins install bookmark` succeeds; `config get` lists defaults |
| 5 | Web UI loads cleanly | ✅ | IBR scan of `localhost:11436`: verdict PASS, 0 console errors, 3 buttons, dashboard intent classified |
| 6 | No regressions | ✅ | **289/289 tests pass** (full suite including ollama + serve); 1 stale test (`test_resolve_model_stale_config_retriggers`) updated to reflect new fallback-first behavior + a new test for the no-fallback path |

## Files modified

- `src/localsmartz/ollama.py` — added `resolve_available_model(requested, min_gb=1.0) -> tuple[str|None, str|None]`
- `src/localsmartz/__main__.py` — `_preflight()` now mutates profile to fallback model with stderr warning instead of hard-exit; `_check()` mirrors the behavior
- `src/localsmartz/config.py` — `resolve_model()` tries fallback before re-triggering first-run picker
- `src/localsmartz/plugins/agent_integration.py` — `_make_command_tool()` now executes fenced `bash`/`sh`/`shell`/`zsh` blocks via `subprocess.run(["bash","-c", body], cwd=workspace, env=stripped, timeout=30)` when `Bash` is in `allowed_tools`
- `app/scripts/embed-python.sh` — real SHA256 pinned, placeholder bypass removed
- `README.md` — repo placeholder replaced

## Files added

- `tests/test_model_fallback.py` (7 tests)
- `tests/test_plugins_exec.py` (14 tests)

## End-to-end verification (live)

```
$ localsmartz --version
localsmartz 0.1.0

$ localsmartz --check
...
! Model 'llama3.1:70b-instruct-q5_K_M' not pulled — using 'gpt-oss:120b' instead.
Ready to go.

$ localsmartz plugins install ~/Desktop/git-folder/RossLabs-AI-Toolkit/plugins/bookmark
✓ Installed bookmark at /Users/tyroneross/.localsmartz/plugins/bookmark

$ localsmartz plugins list
NAME      VERSION  SKILLS  COMMANDS  DESCRIPTION
bookmark  0.3.2    1       6         Session context continuity ...

$ localsmartz config get
KEY            VALUE                                                            SOURCE
workspace      /Users/tyroneross/Documents/LocalSmartz                          default
python_path    /Users/tyroneross/.local/share/uv/tools/localsmartz/bin/python3  default
plugin_paths   /Users/tyroneross/Desktop/git-folder/RossLabs-AI-Toolkit         default
...

$ localsmartz --serve --port 11436 &
$ curl http://localhost:11436/api/status
{"ready": true, "missing_models": [], ...}

$ # IBR scan: PASS, 0 errors, 3 buttons
```

## Decision points encoded

- **Trust model for plugin exec**: trusted (user explicitly installed) → subprocess + timeout + stripped env, no bubblewrap. Path to richer sandboxing left open.
- **PBS pinning policy**: pin one tag (`20240814`, Python 3.12.5); manual bumps. Reproducible.
- **Fallback strategy**: largest available model ≥ 1GB wins; warning printed; original recommendation preserved in pull command for the user.

## Known caveats (unchanged from prior loop)

- Apple Developer ID signing/notarization deferred (user explicitly out of scope)
- MCP integration still opt-in via `include_mcp=True` (default `False`); flip later when needed
- Plugin command exec runs first fenced shell block only — multi-block commands need a future "step picker"
