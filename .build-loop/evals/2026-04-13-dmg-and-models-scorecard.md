# Build-Loop Scorecard — DMG + Models Tab + Icon (2026-04-13)

## Result: ✅ DMG installs cleanly on a fresh profile and auto-detects bundled Python

| # | Goal | Status | Evidence |
|---|---|---|---|
| Icon | Custom dock icon | ✅ | `app/scripts/make-icon.py` generates indigo squircle + magnifying glass + chip lens; all 10 AppIcon.appiconset sizes + Contents.json written; `ASSETCATALOG_COMPILER_APPICON_NAME: AppIcon` wired in project.yml |
| Models tab + toggles | Install/remove models from Settings | ✅ | `/api/models/catalog` returns 8 curated entries with `installed` flag + actual size; `/api/models/pull` streams SSE; `/api/models/delete` via `ollama rm`; new `ModelsTab.swift` binds all three |
| Ollama location | Show models_path + disk usage | ✅ | `/api/ollama/info` returns `models_path`, `source` (default vs OLLAMA_MODELS), total bytes, count; surfaced in Models tab "Ollama storage" section |
| DMG includes Python scripts | Works on another Mac | ✅ | 89 MB DMG; `cpython-3.12.5+20240814` bundled at `Contents/Resources/python/`; `pip install .` run into bundled Python; verified `import localsmartz` works from inside bundle |
| "Choose Python" bypass on fresh Mac | SetupView auto-detects | ✅ | Bundled Python added to candidate list at priority 0; SetupView check_localsmartz path changed from brittle `-m localsmartz --version` to `-c "import localsmartz"` |
| Fallback UI-visible | UI shows ready state | ✅ | `/api/status` now returns `effective_model` + `fallback_warning` when the configured planning_model is missing but a substitute exists — SetupView "Models" row flips to ✅ instead of blocking |

## End-to-end proof (just executed)

```
$ cp "DMG/Local Smartz.app" /Applications/
$ defaults delete com.localsmartz.LocalSmartz   # simulate fresh Mac
$ open "/Applications/Local Smartz.app"
```

IBR scan of the opened app:
- ✅ **Python** — `/Applications/Local Smartz.app/Contents/Resources/python/bin/python3`
- ✅ **Local Smartz** — installed in selected Python
- ✅ **Workspace** — `~/Library/Application Support/LocalSmartz`
- ✅ **Ollama** — Running
- ✅ **Models** — "All required models installed" (fallback active)
- → "Local Smartz is ready." — **[Get Started]** enabled

```
$ curl localhost:11450/api/status
ready=True  effective_model=gpt-oss:120b  missing=[]
fallback_warning=Model 'llama3.1:70b-instruct-q5_K_M' not pulled — using 'gpt-oss:120b' instead.
```

## Query round-trip (earlier in same session)

```
$ curl -N -X POST localhost:11435/api/research -d '{"prompt":"2+2?"}'
data: {"type": "text", "content": "The sum of 2+2 is 4."}
data: {"type": "done", "duration_ms": 8931}
```

## Files added

- `app/scripts/make-icon.py` — PIL-based icon generator
- `app/LocalSmartz/Assets.xcassets/AppIcon.appiconset/` — 10 PNGs + Contents.json
- `app/LocalSmartz/Views/ModelsTab.swift` — ~280 LOC SwiftUI tab

## Files modified

- `src/localsmartz/ollama.py` — `SUGGESTED_MODELS` list, `ollama_models_path()`, `ollama_disk_usage_bytes()`
- `src/localsmartz/serve.py` — 4 new routes (`/api/models/catalog`, `/api/models/pull`, `/api/models/delete`, `/api/ollama/info`); `/api/status` adds `effective_model` + `fallback_warning`
- `app/LocalSmartz/Services/BackendManager.swift` — `resolveBackendSpawn()` replaces `resolvePythonPath()`; prefers bundled Python → localsmartz shim → user pref → uv tool venv; validates via `import localsmartz`; merges sensible PATH into child env
- `app/LocalSmartz/Views/SetupView.swift` — bundled Python added to candidate list; `pythonImportsLocalsmartz` helper replaces brittle `-m localsmartz --version` check
- `app/LocalSmartz/Views/SettingsView.swift` — new "Models" tab in TabView; About tab gains "Reset setup wizard…" button
- `app/LocalSmartz/Views/ResearchView.swift` — model picker in toolbar; `fetchModels()` + `switchModel()` via `/api/models` + `/api/models/select`
- `app/LocalSmartz/Models/AppState.swift` — `resetSetup()` method
- `app/project.yml` — `ASSETCATALOG_COMPILER_APPICON_NAME: AppIcon`

## Test status

- `pytest tests/` — **289 passed** (full suite, no skips for ollama/serve since Ollama is running locally)
- `bash -n` clean on `build-dmg.sh` and `embed-python.sh`
- Mac app `xcodebuild -configuration Debug build` → BUILD SUCCEEDED
- Mac app `xcodebuild -configuration Release` (via `build-dmg.sh`) → BUILD SUCCEEDED

## Cross-Mac install instructions for you

1. On the source Mac: `cd app && bash build-dmg.sh` (produces `LocalSmartz-Installer.dmg`, ~89 MB)
2. Copy DMG to target Mac (AirDrop, Messages, USB, whatever)
3. On target: double-click DMG, drag **Local Smartz** to Applications, launch from /Applications
4. First launch: SetupView will auto-detect the bundled Python and walk through Ollama check + model download
5. No `pip`, no `uv`, no `python3` install required on target Mac — everything is inside the bundle

## Caveats

- Ad-hoc codesign only — on another Mac, the first launch may require right-click → Open to bypass Gatekeeper
- Target Mac still needs Ollama installed (the DMG doesn't bundle Ollama — it's an ~8 GB separate install). If Ollama is missing, SetupView prompts with a link to https://ollama.com/download
- Ollama models (the actual weights) remain per-user in `~/.ollama/models` — the new `/api/ollama/info` endpoint shows their location + disk usage in Settings → Models
