# Local Smartz — Web UI Redesign (Alt C: Creative)

**Date:** 2026-03-22
**Scope:** Redesign embedded web UI with model selector, folder management, thread metadata, and Alt C visual language

## Context

E2E testing revealed three feature gaps in the web UI:
1. **No model selector** — users can't switch models from the browser
2. **Thread sidebar doesn't show metadata** — no entry count or timestamps
3. **No folder/workspace configuration** — no way to tell the agent which folders to use for research

This spec addresses all three while redesigning the UI with the Alt C "Creative" visual direction.

## Visual Language

**Reference mockup:** `/tmp/localsmartz-alt-c-creative.html`

### Design Tokens

| Token | Value | Usage |
|-------|-------|-------|
| `--bg` | `#0c0c0c` | Page background |
| `--surface` | `#161616` | Cards, sidebar |
| `--surface-raised` | `#1c1c1c` | Elevated elements |
| `--border` | `rgba(255,255,255,0.06)` | Dividers |
| `--border-hover` | `rgba(255,255,255,0.12)` | Interactive hover |
| `--fg` | `#e4e4e4` | Primary text |
| `--fg-secondary` | `rgba(228,228,228,0.6)` | Secondary text |
| `--fg-muted` | `rgba(228,228,228,0.3)` | Muted/labels |
| `--teal` | `#0ea5e9` | Accent, active indicators |
| `--teal-dim` | `rgba(14,165,233,0.15)` | Focus ring, subtle bg |
| `--amber` | `#f59e0b` | Secondary folder indicators |
| `--green` | `#22c55e` | Status OK |
| `--red` | `#ef4444` | Error, stop hover |

### Typography

- **UI text:** `-apple-system, BlinkMacSystemFont, system-ui, sans-serif`
- **Data text (models, paths, times):** `ui-monospace, SFMono-Regular, Menlo, monospace`
- **Panel labels:** 10px, weight 600, uppercase, letter-spacing .06em, `--fg-muted`
- **Card titles:** 12px, weight 500, monospace
- **Headings:** 20px, weight 600, tracking -0.02em
- **Body:** 14px, line-height 1.7
- **Meta:** 10px, tabular-nums

### Spacing

- **Section padding:** 12px 16px
- **Card padding:** 8px 10px
- **Main content padding:** 28px 32px
- **Stack gap (sections):** 1px (grid gap creates borders)

## Layout

```
┌─ 240px sidebar ─┬─ 1fr main ──────────────────────┐
│ Brand (LS icon)  │ Header: "Research"               │
│                  │ Subtitle: "Ask anything..."       │
│ MODEL section    │                                   │
│  [model card]    │ [textarea]                        │
│                  │ [Run] [Stop]                      │
│ FOLDERS section  │                                   │
│  folder-1 (ws)   │ [output pane]                    │
│  folder-2        │                                   │
│  + Add folder    │                                   │
│                  │                                   │
│ THREADS section  │                                   │
│  · thread-1      │                                   │
│  · thread-2      │                                   │
│  · thread-3      │                                   │
│                  │                                   │
│ Status bar       │                                   │
└──────────────────┴───────────────────────────────────┘
```

## Components

### Brand Section
- Gradient icon (28x28, border-radius 6px, teal→indigo gradient)
- "Local Smartz" (13px semibold) + "v0.1.0" (10px muted)
- Bottom border separator

### Model Card
- Panel label: "MODEL"
- Card: model name (12px mono, weight 500) + size/profile below (10px muted)
- "Change" link (10px, teal) — on click, shows model list overlay
- Bottom border separator

### Model List Overlay
- Replaces model card content when "Change" is clicked
- Lists all models from `/api/models`: name + size
- Click selects → saves via `POST /api/models/select` → card updates
- "Cancel" link to close overlay

### Folders Section
- Panel label: "FOLDERS"
- List items: colored dot (4px, teal for workspace, amber for added) + path (11px mono)
- Workspace folder always first, non-removable, labeled implicitly
- Added folders have remove button (x)
- "+ Add folder" dashed button at bottom
- Add folder: shows text input for path entry (no file picker in embedded HTML)
- Bottom border separator

### Threads Section
- Panel label: "THREADS"
- Thread items: dot (5px, teal+glow for active, muted for inactive) + title (12px, ellipsis) + meta line (10px: "N entries · time ago")
- Click toggles active thread (same behavior as current)
- Flex: 1 to fill remaining sidebar space

### Status Bar
- Bottom of sidebar, top border
- Green dot (6px, with glow shadow) + "Ollama running · N models"
- If offline: red dot + "Ollama offline"

### Run Button
- Gradient background: `linear-gradient(135deg, #0ea5e9, #6366f1)`
- Box shadow: `0 2px 8px rgba(14,165,233,0.15)`
- Hover: shadow expands, translateY(-1px)
- Disabled: opacity 0.3

### Output Pane
- Same behavior as current (SSE streaming, tool badges, done line)
- Updated styling to match Alt C tokens

## API Changes

### `GET /api/models`

Returns available Ollama models with sizes.

```json
{
  "models": [
    {"name": "gpt-oss:120b", "size_gb": 120.0},
    {"name": "qwen3:8b-q4_K_M", "size_gb": 5.0}
  ],
  "current": "gpt-oss:120b",
  "profile": "full"
}
```

Implementation: calls `list_models_with_size()` from `ollama.py`, reads current from config or profile default.

### `POST /api/models/select`

Switches the active model.

```json
// Request
{"model": "qwen3:8b-q4_K_M"}

// Response
{"ok": true, "model": "qwen3:8b-q4_K_M", "profile": "full"}
```

Implementation: validates model exists in Ollama, calls `save_config()`, updates the handler's `_default_profile` model override.

### `GET /api/folders`

Returns configured research folders.

```json
{
  "workspace": "/Users/tyroneross/Desktop/git-folder/local-smartz",
  "folders": ["/Users/tyroneross/Documents/research-data"]
}
```

### `POST /api/folders`

Adds a research folder.

```json
// Request
{"path": "~/Documents/research-data"}

// Response
{"ok": true, "folders": ["/Users/tyroneross/Documents/research-data"]}
```

Validates path exists and is a directory. Expands `~`. Saves to config.

### `DELETE /api/folders`

Removes a research folder.

```json
// Request
{"path": "~/Documents/research-data"}

// Response
{"ok": true, "folders": []}
```

### Thread sidebar fix

The `done` SSE event already triggers `fetchThreads()`. The issue is likely a race condition — thread entry not yet written when the client fetches. Fix: add `thread_id` to the done event so the UI can optimistically show the new thread, then confirm on next fetch.

## Config Changes

`config.py` — extend `load_config`/`save_config` to handle `folders` key:

```python
# .localsmartz/config.json
{
  "planning_model": "gpt-oss:120b",
  "profile": "full",
  "folders": ["~/Documents/research-data"]
}
```

- `folders` is a list of absolute or `~`-prefixed paths
- Workspace (cwd) is NOT stored — always implicit
- `load_config()` returns `folders` as `[]` if missing
- `save_config()` preserves existing keys when updating

## Files to Modify

| File | Changes |
|------|---------|
| `src/localsmartz/serve.py` | Replace `_UI_HTML` (lines 56-284). Add `_handle_models()`, `_handle_model_select()`, `_handle_folders()`, `_handle_folder_add()`, `_handle_folder_delete()`. Update `do_GET`/`do_POST`/`do_DELETE` routing. Add `thread_id` to done event. |
| `src/localsmartz/config.py` | Add `folders` support: `get_folders(cwd)`, `add_folder(cwd, path)`, `remove_folder(cwd, path)`. Ensure `save_config()` merges keys. |
| `tests/test_serve.py` | Add tests for `/api/models`, `/api/models/select`, `/api/folders` endpoints |
| `tests/test_config.py` | Add tests for folders config CRUD |

## Testing

After implementation:
1. Re-run E2E Test 3 (IBR structural scan) — verify new layout matches Alt C
2. Re-run E2E Test 4 (IBR interactive session) — verify model selector works
3. New test: change model via UI → verify config.json updated
4. New test: add/remove folder via UI → verify config.json updated
5. New test: thread sidebar updates after query completion
6. Run existing unit tests (153) — must all still pass

## Success Criteria

- Model selector visible in sidebar, functional (list → select → save)
- Folders section shows workspace + configurable additional folders
- Thread sidebar shows entry count + relative time
- Thread sidebar updates after research completes
- All existing functionality preserved
- Alt C visual language applied consistently
- Dark mode only (no light mode variant needed)
