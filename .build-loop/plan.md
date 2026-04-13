# Execution Plan — local-smartz upgrade

## Key constraints discovered in Phase 1

- CLI is argparse with `action="store_true"` flags (`--serve`, `--setup`, `--check`). Adding `localsmartz plugins install` cleanly = use a subparser layer for new noun-based commands only; preserve old flags for backward-compat.
- Config is **per-cwd** (`cwd/.localsmartz/config.json`). Plugins/skills/global prefs need a **per-user** layer at `~/.localsmartz/`. Add `global_config.py` alongside `config.py`; don't break existing per-project semantics.
- Server is `HTTPServer` at `serve.py:1399`. `ThreadingHTTPServer` is a one-line drop-in (subclass of `HTTPServer` + `ThreadingMixIn`).
- License drift: `pyproject.toml` says `MIT` but `LICENSE` is `FSL-1.1-MIT` (commit `a243bf6`). Fix in install hardening task.
- Swift app spawns Python via NSTask — no signed Python embedding yet. Tighter packaging = use `python-build-standalone` placed inside `.app/Contents/Resources/python/` and update `BackendManager.swift` to prefer bundled Python.
- No existing plugin abstraction; agent.py hardcodes `ALL_TOOLS`. Tool registry must be additive — keep built-in tools; append plugin tools.

## File ownership (no overlapping edits between parallel agents)

| Wave | Agent | Owned files |
|------|-------|-------------|
| A1 | Plugin loader/validator | NEW `src/localsmartz/plugins/__init__.py`, `discovery.py`, `validator.py`, `loader.py`, `registry.py`, `mcp_client.py`; NEW `tests/test_plugins_*.py` |
| A2 | Concurrent server | `src/localsmartz/serve.py` (change `HTTPServer` → `ThreadingHTTPServer`, ~5 line edit); NEW `tests/test_serve_concurrency.py` |
| A3 | Native Setup view | `app/LocalSmartz/Views/SetupView.swift` (rewrite), NEW `app/LocalSmartz/Views/SetupProgressView.swift`, NEW `app/LocalSmartz/Services/SetupSSEClient.swift` |
| A4 | Settings CLI infra | NEW `src/localsmartz/global_config.py`; NEW `tests/test_global_config.py` (does NOT touch `__main__.py` yet) |
| A5 | CI workflows | NEW `.github/workflows/python.yml`, `.github/workflows/macos.yml` |
| A6 | Install hardening | `install.sh`, `README.md`, `pyproject.toml` (license + dep pins) |
| B1 | CLI subparser integration | `src/localsmartz/__main__.py` — wires plugins/skills/config subcommands; depends on A1 + A4 |
| B2 | Skills-as-context wiring | `src/localsmartz/agent.py` — inject active skills into system prompt; depends on A1 |
| B3 | Plugin commands as tools | `src/localsmartz/agent.py` (different section) + `src/localsmartz/plugins/tools_adapter.py`; depends on A1, sequential after B2 |
| B4 | MCP wiring into agent | `src/localsmartz/agent.py` final pass; depends on A1, sequential after B3 |
| B5 | Settings UI Swift | NEW `app/LocalSmartz/Views/SettingsView.swift`, edit `app/LocalSmartz/LocalSmartzApp.swift`; depends on A3 (UI conventions) |
| B6 | Bundled Python packaging | `app/build-dmg.sh`, NEW `app/scripts/embed-python.sh`, edit `app/LocalSmartz/Services/BackendManager.swift`; depends on A6 (pyproject pins) |
| C | Validation & iteration | runs pytest, scanners, scorecard |

agent.py is touched by B2/B3/B4 sequentially (one merge point) — not parallel — to avoid conflicts.

## Integration contracts

**Plugin loader API (used by B1, B2, B3, B4)**:
```python
from localsmartz.plugins import Registry, ValidationError

reg = Registry.from_default_root()  # ~/.localsmartz/plugins/
reg.list_plugins() -> list[Plugin]
reg.list_skills(active_only=False) -> list[Skill]
reg.list_commands() -> list[Command]
reg.list_mcp_servers() -> list[MCPServer]
reg.validate(path) -> ValidationReport  # actionable errors
reg.install(source_path, dest_name=None) -> Plugin  # symlink default; --copy flag for copy
```

**Skill activation API (used by B2)**:
```python
from localsmartz.global_config import get_active_skills, set_active_skills
```

**MCP client minimal contract (A1)**:
- stdio JSON-RPC 2.0 client
- `MCPClient.start(command, args, env)` -> spawns process
- `MCPClient.list_tools()` -> returns tool schemas
- `MCPClient.call_tool(name, args)` -> returns result
- Use the `mcp` PyPI package if available; fall back to handcrafted client. Add `mcp` to optional dependency group.

## Coordination checkpoints

1. **After Wave A**: verify all A-tasks land cleanly without merge conflicts. Run pytest — existing tests must still pass.
2. **After B1**: smoke-test new CLI subcommands.
3. **After B2/B3/B4 sequentially**: pytest covering each agent integration.
4. **After Wave B**: full pytest + manual Swift build.

## Optimization notes

- Plugin loader and MCP client share JSON schema validation — single helper.
- Native Setup and Settings UI share Calm Precision conventions — load the skill once, used by both.
- CI workflow can be drafted from the existing test layout — no research needed.
- `python-build-standalone` lookup: WebFetch latest URL once before B6.
