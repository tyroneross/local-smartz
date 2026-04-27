# Routing, telemetry, and package readiness - 2026-04-27

## Routing guards

- Query DOE guard: `python -m localsmartz.query_doe --repetitions 50 --score-only --min-score 2950`
  - Result: `2996.665`
- Agent-role DOE guard: `python -m localsmartz.agent_role_doe --repetitions 50 --score-only --min-score 2950`
  - Result: `2993.5`

## Live server timing smoke

Command:

```bash
./.venv/bin/python -m localsmartz.server_timing \
  --base-url http://127.0.0.1:11435 \
  --runs 1 \
  --limit 2 \
  --timeout 180 \
  --json
```

Results:

| Case | Expected runtime | Actual runtime | First text | Warmup |
| --- | --- | --- | ---: | ---: |
| fast_math | fast_path | fast_path | 4255 ms | 4041 ms |
| current_data | graph_pipeline | graph_pipeline | 2765 ms | 2691 ms |

These values include model warmup and are not steady-state latency claims. The
purpose of this smoke is route correctness plus first-text timing coverage.

## Telemetry privacy

OpenTelemetry export remains guarded by the PII redacting span exporter. This
pass adds redaction coverage for bearer tokens and local filesystem paths before
spans leave the process.

## Package artifact policy

Generated release outputs stay local and out of git. `app/LocalSmartz-Installer.dmg`
is removed from tracking and `*.dmg`, `*.dmg.tmp`, and `*.app` are ignored.

## macOS package verification

- Embedded runtime: Python 3.14.4 from python-build-standalone `20260414`.
- Bundle smoke: `python3 --version` and `import localsmartz; from deepagents import create_deep_agent` both pass.
- Signing smoke: `codesign --verify --deep --strict` passes on `app/build/Build/Products/Release/Local Smartz.app`.
- Bytecode policy: app launches set `PYTHONDONTWRITEBYTECODE=1`; package smoke also runs with it, leaving zero `__pycache__` directories under bundled Python.
- DMG: `hdiutil create` is blocked in this environment with `Device not configured`; local deploy image is produced with `hdiutil makehybrid` at `app/LocalSmartz-Installer.dmg`.
- Visual caveat: Computer Use resolves `com.localsmartz.LocalSmartz` to the installed `/Applications/Local Smartz.app`, so it shows the older installed toolbar. The rebuilt package binary contains the SwiftUI popover model picker and the backend reports `effective_model = gpt-oss:20b`.
